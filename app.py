"""听记 — Windows desktop video transcription workbench."""
from __future__ import annotations

import copy
import json
import os
import queue
import threading
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from core import AppError, Cancelled, export_text, new_document, save_document, timestamp
from local_service import ACTIVE_STATUSES, local_status, run_local_pipeline, reconcile_interrupted_document, recovery_info
from desktop_ui import BG, PAPER, INK, MUTED, ACCENT, HOVER, SOFT, configure_theme, build_ui, sync_document_ui
from workspace_actions import WorkspaceActions
from audio_preview import PreviewError

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


class App(WorkspaceActions, ctk.CTk):
    def __init__(self):
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Tingji.Local.Desktop")
            except (AttributeError, OSError):
                pass
        super().__init__()
        icon = ROOT / "assets" / "tingji.ico"
        if os.name == "nt" and icon.is_file():
            self.iconbitmap(default=str(icon))
            self.iconbitmap(str(icon))
        self.title("听记 · 音视频文稿工作台")
        self.geometry("1240x820")
        self.minsize(960, 680)
        self.configure(fg_color=BG)
        self.doc = None
        self._saved_snapshot = None
        self._disk_snapshot = None
        self.history_docs = []
        self._history_all = []
        self._job_document_id = None
        self._job_snapshot = None
        self._job_saved_snapshot = None
        self._job_persisted = True
        self.worker = None
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.selected = None
        self.loading = False
        self.closing = False
        self.file_path = tk.StringVar()
        self.device = tk.StringVar(value="自动（优先显卡）")
        self.models_label = tk.StringVar(value="正在检查模型…")
        self.auto_summary = tk.BooleanVar(value=True)
        self.summary_style = tk.StringVar(value="通用摘要")
        self.speaker_count = tk.StringVar(value="自动")
        self.language = tk.StringVar(value="自动检测")
        self.language_status = tk.StringVar(value="自动抽样判断原音语言；不确定时先确认，再转写。")
        self.recovery_status = tk.StringVar()
        self._recovery = {"available": False}
        self.save_status = tk.StringVar(value="修改自动保存")
        self.notice_text = tk.StringVar(value="")
        self.status = tk.StringVar(value="选择音视频开始，或打开演示文稿体验阅读与校对。")
        self.title_text = tk.StringVar(value="新建转写")
        self.meta = tk.StringVar(value="导入音视频，整理成可阅读、可编辑的文稿。")
        self.init_workspace_state()
        self._style()
        self._build()
        self.bind_workspace()
        self.check_models()
        self.refresh_history()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_timer = self.after(120, self.drain_events)

    def _style(self):
        configure_theme(self)

    def _build(self):
        build_ui(self)

    @property
    def busy(self):
        return self.worker is not None

    @property
    def document_locked(self):
        return self.busy or bool(self.doc and self.doc.get("status") in ACTIVE_STATUSES | {"状态待核实"})

    def refresh_history(self):
        self._history_all = []
        if RESULTS.exists():
            for path in sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    if doc.get("schema") == 1:
                        entry = {key: doc.get(key) for key in ("id", "title", "language", "status", "created_at", "duration_ms")}
                        entry["status"] = reconcile_interrupted_document(doc)["status"]
                        self._history_all.append(entry)
                except (OSError, ValueError):
                    continue
        self.filter_history()

    def filter_history(self):
        query = self.history_query.get().strip().casefold()
        self.history_docs = [entry for entry in self._history_all if not query or
                             query in (" ".join(str(entry.get(key) or "") for key in ("title", "created_at", "status", "language")) +
                                       " " + {"en": "英文", "zh": "中文"}.get(entry.get("language"), "")).casefold()]
        self.history.delete(0, "end")
        for doc in self.history_docs:
            language = {"en": "英", "zh": "中"}.get(doc.get("language"), "稿")
            self.history.insert("end", f"[{language}] {doc['title']}")
        for index, entry in enumerate(self.history_docs):
            if self.doc and entry["id"] == self.doc["id"]:
                self.history.selection_set(index)
                break
        if self.history_docs:
            self.history_empty.place_forget()
        else:
            self.history_empty.configure(text="没有匹配的文稿" if query else "还没有保存的文稿")
            self.history_empty.place(x=8, y=8)

    def open_history(self, _event=None):
        if self.history.curselection():
            entry = self.history_docs[self.history.curselection()[0]]
            if not self.flush_edits():
                return
            try:
                # History entries are a navigation index, never an editable snapshot.
                document = json.loads((RESULTS / (entry["id"] + ".json")).read_text(encoding="utf-8"))
                self.load_document(document)
            except (OSError, ValueError, KeyError):
                self.show_error("无法打开这份文稿。请检查文稿文件是否仍在原位置。")

    def choose_file(self):
        if self.busy or not self.flush_edits():
            return
        path = filedialog.askopenfilename(title="选择要转写的视频或音频", filetypes=[("视频和音频", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma"), ("所有文件", "*.*")])
        if path:
            self.load_document(new_document(path))
            self.status.set("默认自动判断原音语言，也可手动选择中文或英文。摘要默认中文。")

    def load_document(self, doc, *, persisted=True):
        same_document = bool(self.doc and self.doc["id"] == doc["id"])
        previously_had_text = bool(self.doc and self.doc["segments"])
        if not same_document:
            self.stop_audio()
            self.search_query.set("")
            self._editor_open = False
            self._summary_editing = False
        if not same_document or (not previously_had_text and doc["segments"]):
            self._settings_open = not bool(doc["segments"])
        selected = self.selected if same_document else None
        current_tab = self.tabs.get() if same_document else "文字稿"
        self.loading = True
        if persisted:
            self._disk_snapshot = copy.deepcopy(doc)
        own_running_task = self.busy and doc["id"] == self._job_document_id
        doc = copy.deepcopy(doc) if own_running_task else reconcile_interrupted_document(doc)
        self.doc = doc
        summary_job = doc.get('summary_job', {})
        summary_style = (summary_job.get('style') if summary_job and not summary_job.get('complete')
                         else doc.get('summary_style'))
        if summary_style in ('通用摘要', '会议纪要'):
            self.summary_style.set(summary_style)
        if persisted:
            self._saved_snapshot = copy.deepcopy(doc)
        self.language.set({"en": "英文", "zh": "中文", "auto": "自动检测"}.get(doc.get("language_mode", doc.get("language", "auto")), "自动检测"))
        settings = doc.get("transcription_settings")
        if settings is None:
            settings = recovery_info(doc, RESULTS).get("settings", {})
        count = settings.get("speaker_count", 0)
        self.speaker_count.set(str(count) if count else "自动")
        detection = doc.get("language_detection", {})
        if detection.get("needs_confirmation") and not doc.get("language"):
            self.language_status.set("未能可靠判断语言，请手动选择中文或英文，再点击「继续转写」。")
        elif doc.get("language"):
            name = {"en": "英文", "zh": "中文"}.get(doc["language"], doc["language"])
            self.language_status.set(("自动检测结果：" if doc.get("language_mode") == "auto" else "本次原音语言：") + name + " · 摘要默认中文")
        else:
            self.language_status.set("自动抽样判断原音语言；不确定时先确认，再转写。")
        self.selected = None
        self.title_text.set(doc["title"])
        self.file_path.set(doc.get("source", ""))
        self.update_metadata()
        self.reader.set_document(doc["segments"], doc["speakers"], selected=selected or 0)
        for editor, content in [(self.segment_editor, ""), (self.summary_editor, doc.get("summary", ""))]:
            editor.configure(state="normal")
            editor.delete("1.0", "end")
            editor.insert("1.0", content)
            editor.edit_modified(False)
            editor.edit_reset()
        self.loading = False
        self.set_busy(self.busy)
        if not persisted and doc.get("segments"):
            self.save_status.set("保存失败 · 可导出备份")
        else:
            self.save_status.set("已保存" if (RESULTS / (doc["id"] + ".json")).is_file() else "修改自动保存")
        self.status.set(doc.get("error") or ("原文已修改，建议重新生成摘要。" if doc.get("summary_stale") else doc["status"]))
        sync_document_ui(self)
        self.tabs.set(current_tab)
        self.update_settings_visibility()
        self.update_editor_visibility()
        self.update_summary_state()
        self.refresh_summary_links()
        self.apply_search()
        if doc.get("error") and doc.get("status") != "已停止":
            self.show_error(doc["error"])
        else:
            self.clear_error()
        if doc["segments"]:
            index = selected if selected is not None and selected < len(doc["segments"]) else 0
            if self.search_query.get() and self.reader.selected_index is not None:
                index = self.reader.selected_index
            self.reader.select(index, notify=False)
            self.select_segment(index)
        self.update_task_bar()

    def set_busy(self, value):
        locked = value or self.document_locked
        if value:
            self.clear_error()
            self.progress.pack(side="right", padx=14)
        else:
            self.progress.pack_forget()
        for w in [self.start_button, self.choose_button, self.example_button, self.summary_check]:
            w.configure(state="disabled" if value else "normal")
        self.start_button.configure(state="disabled" if locked else "normal")
        self.summary_check.configure(state="disabled" if locked else "normal")
        self.history.configure(state="normal")
        self.new_button.configure(state="disabled" if value else "normal")
        for w in [self.device_box, self.count_box, self.style_box, self.language_box]:
            w.configure(state="disabled" if locked else "readonly")
        has_text = bool(self.doc and self.doc["segments"])
        self.stop_button.configure(state="normal" if value else "disabled")
        self.summary_button.configure(state="normal" if not locked and has_text else "disabled")
        self.rename_button.configure(state="normal" if not locked and has_text else "disabled")
        self.segment_editor.configure(state="normal" if not locked and has_text else "disabled")
        self.summary_editor.configure(state="normal" if not locked and has_text else "disabled")
        self.summary_edit_button.configure(state="normal" if not locked and has_text else "disabled")
        if locked:
            self._summary_editing = False
        self.update_summary_mode()
        self.rename_document_button.configure(state="normal" if self.doc and not locked else "disabled")
        self.speaker_box.configure(state="readonly" if has_text and not locked else "disabled")
        self.merge_button.configure(state="normal" if has_text and not locked and len(self.doc["speakers"]) > 1 else "disabled")
        self.restore_summary_button.configure(state="normal" if self.doc and self.doc.get("previous_summary") and not locked else "disabled")
        self.relink_button.configure(state="normal" if self.doc and not locked else "disabled")
        self.editor_toggle_button.configure(state="normal" if has_text and not locked else "disabled")
        for button in (self.play_button, self.replay_button, self.previous_segment_button, self.next_segment_button):
            button.configure(state="normal" if has_text else "disabled")
        for button in self.export_buttons:
            button.configure(state="normal" if has_text else "disabled")
        self.update_task_bar(force_busy=value)
        self.update_recovery_ui(locked)

    def update_recovery_ui(self, locked=None):
        locked = self.document_locked if locked is None else locked
        self._recovery = recovery_info(self.doc, RESULTS)
        available = self._recovery["available"]
        if available:
            if not locked and not self.doc.get("segments"):
                self._settings_open = True
                self.update_settings_visibility()
            self.resume_button.pack(side="right", padx=(0, 8))
            self.resume_button.configure(state="disabled" if locked else "normal")
            saved = self._recovery["completed_ms"]
            total = self._recovery["total_ms"]
            self.recovery_status.set(f"可继续：已保存识别 {timestamp(saved)} / {timestamp(total)}；仅重算未完成的片段。" if total else "音频准备尚未完成，继续时会重新准备音频。")
            if self._recovery["total_chunks"] and self._recovery["completed_chunks"] == self._recovery["total_chunks"]:
                self.recovery_status.set("识别片段已全部保存；继续完成说话人分组和文稿整理。")
        else:
            self.resume_button.pack_forget()
            self.recovery_status.set(self._recovery.get("reason", ""))
        self.start_button.configure(text="重新转写" if available or self._recovery.get("reason") else "开始转写")
        self.start_button.configure(fg_color=SOFT if available else ACCENT,
                                    hover_color=SOFT if available else HOVER,
                                    text_color=INK if available else PAPER)
        self.source_relink_button.configure(state="normal" if self.doc and not locked else "disabled")

    def update_task_bar(self, force_busy=None):
        running = self.busy if force_busy is None else force_busy
        elsewhere = self._job_snapshot and (not self.doc or self.doc["id"] != self._job_snapshot["id"])
        if running or elsewhere:
            self.task_bar.grid()
        else:
            self.task_bar.grid_remove()
        self.return_task_button.configure(state="normal" if self._job_snapshot and elsewhere else "disabled")

    def return_to_task(self):
        if not self._job_snapshot or not self.flush_edits():
            return
        snapshot = self._job_snapshot
        if not self.busy and self._job_persisted:
            try:
                snapshot = json.loads((RESULTS / (snapshot["id"] + ".json")).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.show_error("无法读取任务文稿，请检查文稿文件。")
                return
        self.load_document(snapshot, persisted=self._job_persisted)

    def check_models(self):
        state = local_status()
        self.models_label.set("中文识别：" + ("已就绪" if state["asr"] else "尚未安装完整") +
                              "\n英文识别：" + ("已就绪" if state.get("english") else "尚未安装") +
                              "\n语言检测：" + ("已就绪" if state.get("language_detection") else "可手动选择语言") +
                              "\n摘要模型：" + (("已就绪（支持显卡）" if state.get('summary_gpu') else "已就绪（CPU）") if state["summary"] else "尚未安装完整"))
        return state

    def check_ready(self, summary_only=False, resume=False):
        if not summary_only and self.language.get() not in ("自动检测", "中文", "英文"):
            message = "请选择自动检测、中文或英文。摘要默认输出中文，不影响原文语言。"
            self.show_error(message)
            messagebox.showinfo("选择原音语言", message, parent=self)
            return False
        state = self.check_models()
        selected = {"自动检测": "auto", "中文": "zh", "英文": "en"}.get(self.language.get())
        if resume and selected == "auto":
            selected = self._recovery.get("language") or selected
        if not summary_only and selected == "auto" and not state.get("language_detection"):
            message = "自动检测模型尚未就绪。请运行「安装语言检测模型.cmd」，或手动选择中文／英文后开始。"
            self.show_error(message)
            messagebox.showinfo("安装语言检测模型", message, parent=self)
            return False
        if not summary_only and selected == "en" and not state.get("english"):
            message = "英文模型尚未安装完整，请运行“安装英文模型.cmd”，完成后重新打开程序。英文文字稿保留原文，摘要默认使用中文。"
            self.show_error(message)
            messagebox.showinfo("安装英文模型", message, parent=self)
            return False
        if not summary_only and selected == "zh" and not state["asr"]:
            self.show_error("中文模型尚未就绪，请运行“安装本地模型.cmd”后重试。")
            messagebox.showinfo("安装本地模型", "请先运行项目中的“安装本地模型.cmd”。首次安装需联网下载，之后转写可离线使用。")
            return False
        if summary_only and not state["summary"]:
            messagebox.showinfo("安装摘要模型", "摘要模型尚未安装完整。请运行“安装本地模型.cmd”；已完成的文字稿仍可编辑和导出。")
            return False
        if not summary_only and self.auto_summary.get() and not state["summary"]:
            messagebox.showinfo("摘要尚未就绪", "请取消勾选“生成摘要”，先转写；或安装本地摘要模型后再开始。")
            return False
        return True

    def start(self):
        if self.document_locked:
            return
        if not self.doc or not self.file_path.get() or not Path(self.file_path.get()).is_file():
            messagebox.showinfo("选择文件", "请先选择本机的视频或音频文件。")
            return
        if not self.check_ready():
            return
        if not self.flush_edits():
            return
        if self.doc["segments"] or self._recovery.get("available") or self._recovery.get("reason"):
            if not messagebox.askyesno("重新转写", "重新转写会创建一份新文稿，原文稿和恢复进度仍保留。是否继续？", default="no", parent=self):
                return
            self.doc = new_document(self.file_path.get())
            self._saved_snapshot = self._disk_snapshot = None
        elif self.doc.get("task_id"):
            self.doc = new_document(self.file_path.get())
            self._saved_snapshot = self._disk_snapshot = None
        self.launch_job()

    def resume_transcription(self):
        if self.document_locked or not self.doc:
            return
        self.update_recovery_ui()
        if not self._recovery["available"]:
            self.show_error(self._recovery.get("reason") or "没有可恢复的转写进度。")
            return
        if self.check_ready(resume=True):
            self.launch_job(resume=True)

    def summarize(self):
        if not self.document_locked and self.doc and self.doc["segments"] and self.check_ready(summary_only=True):
            self.launch_job(summary_only=True)

    def restart_summary(self):
        if not self.document_locked and self.doc and self.flush_edits(verify_current=True):
            self.doc.pop('summary_job', None)
            self.summarize()

    def launch_job(self, summary_only=False, resume=False):
        if self.document_locked:
            return
        if not summary_only and self.language.get() not in ("自动检测", "中文", "英文"):
            self.check_ready()
            return
        if not self.flush_edits(verify_current=True):
            return
        device = "cpu" if self.device.get() == "仅 CPU" else "auto"
        document = copy.deepcopy(self.doc)
        if summary_only and document.get("summary"):
            from summary_state import snapshot
            document["previous_summary"] = snapshot(document)
        self._job_document_id = document["id"]
        self._job_snapshot = copy.deepcopy(document)
        self._job_saved_snapshot = copy.deepcopy(self._disk_snapshot)
        expected_document = copy.deepcopy(self._disk_snapshot)
        self._job_persisted = True
        self.stop_audio()
        style, auto = self.summary_style.get(), self.auto_summary.get()
        count = 0 if self.speaker_count.get() == "自动" else int(self.speaker_count.get())
        language = {"英文": "en", "中文": "zh", "自动检测": "auto"}.get(self.language.get(), "auto")
        self.cancel = threading.Event()
        self.set_busy(True)
        self.progress.start(12)
        self.show_progress("正在准备本地处理，请稍候…")

        def work():
            error = ""
            try:
                run_local_pipeline(document, RESULTS, self.cancel, lambda text: self.events.put(("progress", text)),
                                   summary_only=summary_only, auto_summary=auto, style=style, speaker_count=count, device=device, language=language,
                                   on_document=lambda snapshot: self.events.put(("document", snapshot)), resume=resume,
                                   expected_document=expected_document)
            except AppError as exc:
                error = str(exc)
            except Exception:
                error = "本地处理未完成，请查看保存状态。界面中已有的文字仍可导出备份。"
            self.events.put(("done", (document, error)))

        self.worker = threading.Thread(target=work, name="transcription-worker", daemon=False)
        self.worker.start()

    def drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.show_progress(value)
                elif kind == "document":
                    self._job_snapshot = copy.deepcopy(value)
                    self._job_saved_snapshot = copy.deepcopy(value)
                    self._job_persisted = True
                    if self.doc and self.doc["id"] == value["id"]:
                        self.load_document(value)
                    self.refresh_history()
                    self.update_task_bar()
                    if self.doc and self.doc["id"] == value["id"] and value.get("segments") and value["status"] in ("已转写", "生成摘要"):
                        self.status.set("文字稿已保存，可先阅读和导出。" + ("摘要正在生成，完成后可校对。" if value["status"] == "生成摘要" else ""))
                elif kind == "done":
                    self.worker.join(timeout=1)
                    self.worker = None
                    self.progress.stop()
                    document, error = value
                    try:
                        disk = json.loads((RESULTS / (document["id"] + ".json")).read_text(encoding="utf-8"))
                        persisted = disk == document
                    except (OSError, ValueError):
                        persisted = False
                    self._job_snapshot = copy.deepcopy(document)
                    self._job_persisted = persisted
                    if not persisted:
                        self._disk_snapshot = copy.deepcopy(self._job_saved_snapshot)
                        self._saved_snapshot = copy.deepcopy(self._job_saved_snapshot)
                    if not self.doc or self.doc["id"] == document["id"] or not persisted:
                        self.load_document(document, persisted=persisted)
                    else:
                        self.set_busy(False)
                    self.refresh_history()
                    self.task_status.set(error or "任务已完成，可返回查看文稿。")
                    if self.doc and self.doc["id"] == document["id"]:
                        self.status.set(error or "已保存到本机。可校对文字、修改说话人姓名并导出。")
                    self.update_task_bar()
                    if self.closing:
                        self.closing = False
                        if self.flush_edits() and self.destroy():
                            return
                    if error and document.get("status") != "已停止":
                        self.show_error(error)
                        messagebox.showerror("处理未完成", error, parent=self)
        except queue.Empty:
            pass
        self.poll_timer = self.after(120, self.drain_events)

    def show_error(self, message):
        self.notice_text.set(str(message)[:350])
        self.error_panel.grid()
        if not self.doc or not self.doc["segments"]:
            self.empty_title.configure(text="处理未完成")
            self.empty_caption.configure(text="请查看上方错误原因，修正后重新开始。")

    def clear_error(self):
        self.notice_text.set("")
        self.error_panel.grid_remove()
        self.reload_button.pack_forget()

    def show_progress(self, message):
        title = (self._job_snapshot or {}).get("title", "")
        self.task_status.set((title + " · " if title else "") + message)
        if self.doc and self.doc["id"] != self._job_document_id:
            return
        if not self.doc or not self.doc["segments"]:
            self.empty_title.configure(text="正在本机处理")
            self.empty_caption.configure(text=message)

    def destroy(self):
        try:
            self.audio.close()
        except PreviewError as exc:
            self.audio_hint.set(str(exc))
            messagebox.showerror("暂时无法关闭回听", str(exc), parent=self)
            return False
        for attribute in ("poll_timer", "save_timer", "search_timer", "audio_timer"):
            timer = getattr(self, attribute, None)
            if timer:
                self.after_cancel(timer)
                setattr(self, attribute, None)
        # Each root owns its Tcl interpreter; cancel its widget/DPI callbacks too.
        for timer in self.tk.call("after", "info"):
            self.tk.call("after", "cancel", timer)
        super().destroy()
        return True

    def stop(self):
        self.cancel.set()
        self.stop_button.configure(state="disabled")
        self.task_status.set("正在停止本地计算并释放资源…已完成的文字稿会保留。")

    def select_segment(self, index=None):
        if self.loading:
            return
        if not self.doc or not self.doc["segments"]:
            return
        self.selected = self.reader.selected_index if index is None else int(index)
        if self.selected is None or not 0 <= self.selected < len(self.doc["segments"]):
            return
        segment = self.doc["segments"][self.selected]
        hint = "任务未结束或状态待核实，当前可阅读和导出" if self.document_locked else "编辑后自动保存"
        self.selection_hint.set(f"第 {self.selected + 1} / {len(self.doc['segments'])} 段 · {timestamp(segment['start_ms'])} · {hint}")
        self.update_speaker_choices()
        self.loading = True
        self.segment_editor.configure(state="normal")
        self.segment_editor.delete("1.0", "end")
        self.segment_editor.insert("1.0", self.doc["segments"][self.selected]["text"])
        self.segment_editor.edit_modified(False)
        self.segment_editor.edit_reset()
        if self.document_locked:
            self.segment_editor.configure(state="disabled")
        self.loading = False

    def segment_changed(self, _event=None):
        if not self.segment_editor.edit_modified():
            return
        self.segment_editor.edit_modified(False)
        if self.loading or self.document_locked or self.selected is None or not self.doc:
            return
        text = self.segment_editor.get("1.0", "end-1c")
        segment = self.doc["segments"][self.selected]
        if segment["text"] != text:
            segment["text"] = text
            self.reader.update_segment(self.selected, segment, self.doc["speakers"])
            self.doc["summary_stale"] = bool(self.doc["summary"])
            self.update_summary_state()
            self.schedule_save()

    def summary_changed(self, _event=None):
        if not self.summary_editor.edit_modified():
            return
        self.summary_editor.edit_modified(False)
        if self.loading or self.document_locked or not self.doc:
            return
        self.doc["summary"] = self.summary_editor.get("1.0", "end-1c")
        from summary_state import mark_edited
        mark_edited(self.doc)
        self.refresh_summary_links()
        self.update_summary_state()
        self.schedule_save()

    def schedule_save(self):
        self.save_status.set("保存中…")
        if getattr(self, "save_timer", None):
            self.after_cancel(self.save_timer)
        self.save_timer = self.after(500, self.flush_edits)

    def flush_edits(self, *, verify_current=False):
        if getattr(self, "save_timer", None):
            self.after_cancel(self.save_timer)
            self.save_timer = None
        # The pipeline owns writes while computing. Export must never write its
        # earlier UI snapshot over a newer stage saved by the worker.
        if self.document_locked:
            return True
        if self.doc and (self.doc["segments"] or self.doc.get("task_id") or self.doc.get("transcription_settings")
                         or (RESULTS / (self.doc["id"] + ".json")).is_file()):
            try:
                target = RESULTS / (self.doc["id"] + ".json")
                if target.exists():
                    if self.doc == self._saved_snapshot and not verify_current:
                        return True
                    disk_document = json.loads(target.read_text(encoding="utf-8"))
                    if disk_document != self._disk_snapshot:
                        raise AppError("这份文稿已被其他窗口更新。当前修改仍在，请先导出备份，再重新打开最新文稿。")
                    if self.doc == self._saved_snapshot:
                        return True
                save_document(self.doc, RESULTS)
                self._saved_snapshot = copy.deepcopy(self.doc)
                self._disk_snapshot = copy.deepcopy(self.doc)
                if self.doc["id"] == self._job_document_id:
                    self._job_snapshot = copy.deepcopy(self.doc)
                    self._job_saved_snapshot = copy.deepcopy(self.doc)
                    self._job_persisted = True
                self.save_status.set("已保存 · 摘要待更新" if self.doc.get("summary_stale") else "已保存")
            except (OSError, ValueError, AppError) as exc:
                self.save_status.set("保存失败 · 修改仍保留")
                message = str(exc) if isinstance(exc, AppError) else "无法写入文稿文件夹。当前修改仍保留，请检查磁盘空间和目录权限，或导出文稿备份。"
                self.show_error(message)
                self.reload_button.pack(anchor="w", padx=16, pady=(0, 12))
                messagebox.showerror("保存失败", message, parent=self)
                return False
        return True

    def reload_current_document(self):
        if self.busy or not self.doc:
            return
        try:
            document = json.loads((RESULTS / (self.doc["id"] + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            messagebox.showerror("无法重新载入", "无法读取已保存版本。当前修改仍保留，可先导出备份。", parent=self)
            return
        if not messagebox.askyesno("重新载入已保存版本", "这会放弃本窗口尚未保存的修改，并载入磁盘上的最新版本。需要的修改请先导出备份。是否继续？", default="no", parent=self):
            return
        self.load_document(document)
        self.refresh_history()

    def rename_speaker(self):
        if self.document_locked or self.selected is None or not self.doc:
            return
        speaker = self.doc["segments"][self.selected]["speaker"]
        name = simpledialog.askstring("修改说话人", "该说话人的所有段落都会同步更新：", initialvalue=self.doc["speakers"][speaker])
        if name and name.strip():
            self.doc["speakers"][speaker] = " ".join(name.split())[:60]
            self.doc["summary_stale"] = bool(self.doc["summary"])
            self.reader.set_document(self.doc["segments"], self.doc["speakers"], selected=self.selected)
            self.update_speaker_choices()
            self.update_summary_state()
            self.flush_edits()

            self.select_segment()

    def export(self, kind):
        if not self.doc or not self.doc["segments"]:
            return
        self.flush_edits()
        path = filedialog.asksaveasfilename(title="导出文稿", initialfile=self.doc["title"] + "." + kind,
                                          defaultextension="." + kind, filetypes=[(kind.upper(), "*." + kind)])
        if path:
            try:
                Path(path).write_text(export_text(self.doc, kind), encoding="utf-8-sig" if kind in ("txt", "srt") else "utf-8")
                self.status.set("已导出：" + path)
            except OSError:
                messagebox.showerror("导出失败", "文件无法写入，请关闭占用文件的程序，或选择其他文件夹。")

    def open_results(self):
        RESULTS.mkdir(exist_ok=True)
        os.startfile(str(RESULTS))

    def show_help(self):
        window = tk.Toplevel(self)
        window.title("听记 · 使用说明")
        window.geometry("820x620")
        text = ScrolledText(window, wrap="word", padx=20, pady=16, spacing3=6)
        text.pack(fill="both", expand=True)
        try:
            text.insert("1.0", (ROOT / "使用说明.md").read_text(encoding="utf-8-sig"))
        except OSError:
            text.insert("1.0", "选择视频 → 开始转写 → 校对并导出。首次使用请运行“安装本地模型.cmd”。")
        text.configure(state="disabled")

    def open_example(self):
        if self.busy or not self.flush_edits():
            return
        doc = new_document("示例：产品例会（演示数据）")
        doc.update({"status": "示例", "duration_ms": 32000, "speakers": {"0": "林晓", "1": "陈明"},
                    "segments": [{"start_ms": 0, "end_ms": 8500, "speaker": "0", "text": "今天确认一下视频转文字工具的第一版范围：转写、区分说话人，以及摘要。"},
                                 {"start_ms": 9000, "end_ms": 18000, "speaker": "1", "text": "我建议先支持本地文件，文字稿可以校对，再导出字幕。网页链接解析放到后续。"},
                                 {"start_ms": 19000, "end_ms": 26000, "speaker": "0", "text": "可以。我们先拿三段真实视频测试，包含单人讲解和多人讨论。"},
                                 {"start_ms": 27000, "end_ms": 32000, "speaker": "1", "text": "我来准备测试视频，明天下午一起看结果。"}],
                    "summary": "## 概览\n第一版聚焦本地视频的转写、说话人区分与摘要。\n\n## 核心要点\n- 支持文字校对和字幕导出。[00:00:09]\n- 网页链接解析留待后续。[00:00:09]\n\n## 待办\n- 陈明：准备三段测试视频，明天下午共同查看结果。[00:00:27]\n- 覆盖单人讲解和多人讨论。[00:00:19]\n\n这是内置演示数据，并非真实视频识别结果。"})
        self.load_document(doc)
        self.status.set("正在查看演示数据。可体验校对、改名及导出；安装本地模型后可转写自己的视频。")

    def close(self):
        if self.busy:
            if not messagebox.askyesno("退出听记", "退出将停止当前本地计算；已完成的文字稿会保留。是否退出？"):
                return
            self.closing = True
            self.stop()
        else:
            if self.flush_edits():
                self.destroy()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-file", type=Path)
    parser.add_argument("--language", choices=("auto", "zh", "en"))
    args = parser.parse_args()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    application = App()
    if args.open_file and args.open_file.is_file():
        document = new_document(args.open_file.resolve())
        if args.language:
            document["language_mode"] = args.language
            if args.language != "auto":
                document["language"] = args.language
        application.load_document(document)
        application.status.set("已选择英文视频，点击开始转写后在本机处理。" if args.language == "en" else "文件已选择，点击开始转写。")
    application.mainloop()
