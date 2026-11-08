"""Reading, correction and audio-preview actions for the desktop workspace."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from audio_preview import AudioPreview
from core import timestamp

AUDIO_CONTEXT_MS = 3000
AUDIO_IDLE_HINT = "点击时间戳或「回听」核对原音，含前后最多 3 秒上下文"


class WorkspaceActions:
    def init_workspace_state(self):
        self.search_query = tk.StringVar()
        self.search_result = tk.StringVar(value="搜索全文")
        self.history_query = tk.StringVar()
        self.audio_hint = tk.StringVar(value=AUDIO_IDLE_HINT)
        self.summary_state = tk.StringVar(value="尚未生成摘要")
        self.task_status = tk.StringVar()
        self.audio = AudioPreview()
        self._audio_document_id = None
        self._audio_segment_index = None
        self._editor_open = False
        self._settings_open = True
        self._speaker_keys = []

    def bind_workspace(self):
        self.search_query.trace_add("write", self.schedule_search)
        self.history_query.trace_add("write", lambda *_: self.filter_history())
        self.bind("<Control-f>", self.focus_search)
        self.bind("<F3>", lambda _: self.search_next())
        self.bind("<Shift-F3>", lambda _: self.search_previous())
        self.bind("<Control-Return>", lambda _: self.toggle_audio())
        self.bind("<Control-e>", self.focus_editor)
        self.search_entry.bind("<Return>", lambda _: self.search_next())
        self.search_entry.bind("<Shift-Return>", lambda _: self.search_previous())
        self.search_entry.bind("<Escape>", lambda _: self.search_query.set(""))
        self.summary_editor.tag_configure("citation", foreground="#24674E", underline=True)
        self.summary_editor.tag_bind("citation", "<Control-Button-1>", self.follow_summary_citation)
        self.audio_timer = self.after(200, self.poll_audio)
        self.update_editor_visibility()

    def toggle_editor(self):
        if self.document_locked or not self.doc or self.selected is None:
            return
        self._editor_open = not self._editor_open
        self.update_editor_visibility()
        if self._editor_open:
            self.segment_editor.focus_set()

    def focus_editor(self, _event=None):
        if not self.document_locked and self.selected is not None:
            self._editor_open = True
            self.tabs.set("文字稿")
            self.update_editor_visibility()
            self.segment_editor.focus_set()
        return "break"

    def update_editor_visibility(self):
        if self._editor_open:
            self.editor_body.pack(fill="x")
        else:
            self.editor_body.pack_forget()
        self.editor_toggle_button.configure(text="收起校对" if self._editor_open else "校对本段")

    def toggle_settings(self):
        self._settings_open = not self._settings_open
        self.update_settings_visibility()

    def update_settings_visibility(self):
        if self._settings_open:
            self.source_panel.grid()
        else:
            self.source_panel.grid_remove()
        self.settings_button.configure(text="收起设置" if self._settings_open else "转写设置")

    def schedule_search(self, *_):
        if getattr(self, "search_timer", None):
            self.after_cancel(self.search_timer)
        self.search_timer = self.after(150, self.apply_search)

    def apply_search(self):
        if getattr(self, "search_timer", None):
            self.after_cancel(self.search_timer)
            self.search_timer = None
        return self.reader.search(self.search_query.get())

    def search_results_changed(self, current, total):
        self.search_result.set(f"{current} / {total} 处" if total else ("无匹配" if self.search_query.get() else "搜索全文"))
        for name in ("search_prev_button", "search_next_button"):
            if hasattr(self, name):
                getattr(self, name).configure(state="normal" if total else "disabled")

    def focus_search(self, _event=None):
        self.tabs.set("文字稿")
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def search_next(self):
        if getattr(self, "search_timer", None):
            self.apply_search()
        else:
            self.reader.next_match(1)
        return "break"

    def search_previous(self):
        if getattr(self, "search_timer", None):
            self.apply_search()
        self.reader.next_match(-1)
        return "break"

    def previous_segment(self):
        if self.selected is not None:
            self.reader.select(max(0, self.selected - 1))

    def next_segment(self):
        if self.selected is not None:
            self.reader.select(min(self.reader.segment_count - 1, self.selected + 1))

    def update_metadata(self):
        if not self.doc:
            return
        language = {"en": "英文", "zh": "中文"}.get(self.doc.get("language"), "原音语言未指定")
        self.meta.set(f"{timestamp(self.doc.get('duration_ms', 0))}  ·  {language}  ·  "
                      f"{len(self.doc['segments'])} 段文字  ·  {len(self.doc['speakers'])} 位说话人")

    def update_speaker_choices(self):
        self._speaker_keys = list(self.doc["speakers"]) if self.doc else []
        values = [f"{self.doc['speakers'][key]} · 分组 {i + 1}" for i, key in enumerate(self._speaker_keys)]
        self.speaker_box.configure(values=values or ["未区分"])
        if self.selected is not None:
            key = self.doc["segments"][self.selected]["speaker"]
            if key in self._speaker_keys:
                self.speaker_box.set(values[self._speaker_keys.index(key)])
        else:
            self.speaker_box.set("未区分")

    def assign_speaker(self, value):
        if self.document_locked or self.selected is None:
            return
        values = self.speaker_box.cget("values")
        if value not in values:
            return
        key = self._speaker_keys[values.index(value)]
        segment = self.doc["segments"][self.selected]
        if segment["speaker"] == key:
            return
        segment["speaker"] = key
        self.reader.update_segment(self.selected, segment, self.doc["speakers"])
        self.doc["summary_stale"] = bool(self.doc["summary"])
        self.update_summary_state()
        self.select_segment(self.selected)
        self.schedule_save()

    def show_merge_menu(self):
        if self.document_locked or self.selected is None:
            return
        source = self.doc["segments"][self.selected]["speaker"]
        menu = tk.Menu(self, tearoff=False)
        for key, name in self.doc["speakers"].items():
            if key != source:
                menu.add_command(label="合并到 " + name, command=lambda target=key: self.merge_speaker(target))
        if menu.index("end") is not None:
            try:
                menu.tk_popup(self.merge_button.winfo_rootx(),
                              self.merge_button.winfo_rooty() + self.merge_button.winfo_height())
            finally:
                menu.grab_release()

    def merge_speaker(self, target):
        if self.document_locked or self.selected is None or target not in self.doc["speakers"]:
            return
        source = self.doc["segments"][self.selected]["speaker"]
        if source == target:
            return
        names = self.doc["speakers"]
        if not messagebox.askyesno("合并说话人", f"将“{names[source]}”的全部段落归入“{names[target]}”？请确认这两个分组是同一位说话人。", default="no", parent=self):
            return
        for segment in self.doc["segments"]:
            if segment["speaker"] == source:
                segment["speaker"] = target
        del names[source]
        self.doc["summary_stale"] = bool(self.doc["summary"])
        self.reader.set_document(self.doc["segments"], names, selected=self.selected)
        self.update_metadata()
        self.update_speaker_choices()
        self.update_summary_state()
        self.set_busy(self.busy)
        self.select_segment(self.selected)
        self.flush_edits()

    def rename_document(self):
        if not self.doc or self.document_locked:
            return
        title = simpledialog.askstring("文稿名称", "为这份文稿取一个容易找到的名称：", initialvalue=self.doc["title"], parent=self)
        if title and title.strip():
            self.doc["title"] = " ".join(title.split())
            self.title_text.set(self.doc["title"])
            if self.flush_edits():
                self.refresh_history()

    def play_segment(self, index):
        if not self.doc or not 0 <= index < len(self.doc["segments"]):
            return
        self.reader.select(index, notify=True)
        self.play_current_segment()

    def play_current_segment(self):
        if not self.doc or self.selected is None:
            return
        source = self.doc.get("source", "")
        if not source or not Path(source).is_file():
            self.audio_hint.set("找不到原音视频，请用「重新关联」选择同一文件。")
            return
        segment = self.doc["segments"][self.selected]
        start = max(0, segment["start_ms"] - AUDIO_CONTEXT_MS)
        end = segment["end_ms"] + AUDIO_CONTEXT_MS
        if self.doc.get("duration_ms"):
            end = min(end, self.doc["duration_ms"])
        self._audio_document_id = self.doc["id"]
        self._audio_segment_index = self.selected
        self.audio_hint.set(f"准备回听 {timestamp(start)} — {timestamp(end)}")
        try:
            self.audio.play(source, start, end)
        except (OSError, ValueError, RuntimeError) as exc:
            self.audio_hint.set(str(exc))

    def toggle_audio(self):
        state = self.audio.poll()["state"]
        if state == "playing" and self._audio_document_id == (self.doc or {}).get("id"):
            self.audio.pause()
        elif state == "paused" and self._audio_document_id == (self.doc or {}).get("id"):
            self.audio.resume()
        elif state != "preparing":
            self.play_current_segment()
        return "break"

    def stop_audio(self):
        self.audio.stop()
        self._audio_document_id = None
        self._audio_segment_index = None
        self.audio_hint.set(AUDIO_IDLE_HINT)
        if hasattr(self, "play_button"):
            self.play_button.configure(text="回听")

    def poll_audio(self):
        state = self.audio.poll()
        current = self.doc and self.doc["id"] == self._audio_document_id
        if current:
            labels = {"preparing": "准备中…", "playing": "暂停", "paused": "继续"}
            self.play_button.configure(text=labels.get(state["state"], "回听"))
            if state["state"] in ("playing", "paused", "finished"):
                label = {"playing": "正在回听", "paused": "已暂停", "finished": "本段回听结束"}[state["state"]]
                paragraph = f"第 {self._audio_segment_index + 1} 段 · " if self._audio_segment_index is not None else ""
                self.audio_hint.set(f"{label} · {paragraph}{timestamp(state['position_ms'])}")
        # Cleanup errors belong to the player even after changing documents.
        # Keep a stop/retry action available until ownership is released.
        if state["state"] == "error":
            self.audio_hint.set(state.get("error") or "回听失败，请重新关联原音视频。")
        self.audio_stop_button.configure(state="normal" if state["state"] in ("preparing", "playing", "paused", "error") else "disabled")
        self.audio_timer = self.after(200, self.poll_audio)

    def relink_source(self):
        if not self.doc or self.document_locked:
            return
        path = filedialog.askopenfilename(title="重新关联原音视频（请选择同一文件）",
                                         filetypes=[("音视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma"), ("所有文件", "*.*")])
        if path:
            self.stop_audio()
            self.doc["source"] = path
            self.file_path.set(path)
            if self.flush_edits():
                self.audio_hint.set("原音视频已重新关联，点击回听核对。")

    def update_summary_state(self):
        if hasattr(self, 'summary_empty'):
            if self.doc and self.doc.get('summary'):
                self.summary_empty.place_forget()
            else:
                self.summary_empty.configure(text=('正在整理摘要…\n你可以先阅读和导出文字稿。'
                    if self.doc and self.doc.get('status') == '生成摘要'
                    else '提取关键要点，保留原文出处。\n点击下方「生成摘要」开始。'
                    if self.doc and self.doc.get('segments') else '完成转写后，在这里整理摘要。'))
                self.summary_empty.place(relx=.5, rely=.35, anchor='center')
        if self.doc and self.doc.get('status') == '生成摘要':
            text = '正在生成新摘要' + (' · 下方保留上一版' if self.doc.get('summary') else '')
        elif not self.doc or not self.doc.get("summary"):
            text = "尚未生成摘要"
        elif self.doc.get("summary_stale"):
            text = "原文已修改 · 摘要待更新"
        elif "时间待核对" in self.doc["summary"]:
            text = f"摘要已生成 · {self.doc['summary'].count('时间待核对')} 处时间需核对"
        else:
            text = ('已手动编辑 · 要点需核对原文' if self.doc.get('summary_review', {}).get('status') == 'edited'
                    else '摘要已生成 · 要点需核对原文')
        if self.doc and self.doc.get('status') != '生成摘要' and self.doc.get('summary'):
            inference = self.doc.get('summary_inference', {})
            device = inference.get('device')
            if device in ('gpu', 'cpu'):
                label = '显卡摘要' if device == 'gpu' else 'CPU摘要'
                if device == 'cpu' and inference.get('fallback_reason'):
                    label += '（显卡暂不可用）'
                seconds = inference.get('seconds')
                if isinstance(seconds, (int, float)) and math.isfinite(seconds) and 0 <= seconds < 86400:
                    label += ' · ' + timestamp(round(seconds * 1000))
                text = label + ' · ' + text
        missing = (self.doc or {}).get('summary_validation', {}).get('points_need_sources', 0)
        if missing:
            text += f' · {missing} 条要点缺少出处'
        self.summary_state.set(text)

    def refresh_summary_links(self):
        from summary_presentation import format_summary
        format_summary(self.summary_editor)
        self.summary_editor.tag_remove("citation", "1.0", "end")
        allowed = {timestamp(row['start_ms']) for row in (self.doc or {}).get('segments', [])}
        issues = (self.doc or {}).get('summary_validation', {}).get('issues', [])
        for issue in issues:
            if issue.get('kind') in ('unverified_quote', 'unverified_time'):
                allowed.discard(issue.get('time'))
            elif (self.doc.get('summary_review', {}).get('status') == 'edited'
                  and issue.get('kind') == 'corrected_quote_anchor' and issue.get('old_time') != issue.get('time')):
                # Automatically corrected text uses the new time; edited text retains its original.
                allowed.discard(issue.get('old_time'))
        position = "1.0"
        pattern = r"\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]"
        while True:
            match = self.summary_editor.search(pattern, position, stopindex="end", regexp=True)
            if not match:
                break
            end = f"{match}+10c"
            if self.summary_editor.get(match, end).strip('[]') in allowed:
                self.summary_editor.tag_add("citation", match, end)
            position = end

    def follow_summary_citation(self, event):
        position = self.summary_editor.index(f"@{event.x},{event.y}")
        ranges = self.summary_editor.tag_ranges("citation")
        for start, end in zip(ranges[::2], ranges[1::2]):
            if self.summary_editor.compare(start, "<=", position) and self.summary_editor.compare(position, "<", end):
                hours, minutes, seconds = map(int, self.summary_editor.get(start, end).strip("[]").split(":"))
                self.jump_to_time((hours * 3600 + minutes * 60 + seconds) * 1000)
                return "break"

    def jump_to_time(self, position_ms):
        if not self.doc or not self.doc["segments"]:
            return
        candidates = [i for i, segment in enumerate(self.doc["segments"]) if segment["start_ms"] <= position_ms]
        index = candidates[-1] if candidates else 0
        self.tabs.set("文字稿")
        self.reader.select(index)

    def restore_summary(self):
        if self.document_locked or not self.doc or not self.doc.get("previous_summary"):
            return
        from summary_state import restore
        restore(self.doc, self.doc["previous_summary"])
        if self.doc.get('summary_style') in ('通用摘要', '会议纪要'):
            self.summary_style.set(self.doc['summary_style'])
        self.loading = True
        self.summary_editor.delete("1.0", "end")
        self.summary_editor.insert("1.0", self.doc["summary"])
        self.summary_editor.edit_modified(False)
        self.summary_editor.edit_reset()
        self.loading = False
        self.refresh_summary_links()
        self.update_summary_state()
        self.flush_edits()
