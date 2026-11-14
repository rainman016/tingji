"""Reading, correction and audio-preview actions for the desktop workspace."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from audio_preview import AudioPreview
from core import timestamp
from ui_theme import ACCENT, native_font

AUDIO_CONTEXT_MS = 3000
AUDIO_IDLE_HINT = "点击时间戳或「回听」核对原音，含前后最多 3 秒上下文"


class WorkspaceActions:
    def toggle_reading_size(self):
        self._large_reading = not getattr(self, '_large_reading', False)
        self.apply_reading_size()

    def apply_reading_size(self):
        from tkinter import font as tkfont
        size=16 if getattr(self,'_large_reading',False) else 14
        for widget in (self.reader.text,self.segment_editor):
            font=native_font(widget,size)
            spacing=max(0,round(abs(font[1])*1.65)-tkfont.Font(root=widget,font=font).metrics('linespace'))
            widget.configure(font=font,spacing2=spacing)
            widget._reading_size=size
        self.reader.text.tag_configure('metadata',font=native_font(self,12))
        self.reader.update_layout()
        self.reading_size_button.configure(text='标准字号' if size==16 else '放大阅读')

    def init_workspace_state(self):
        self.search_query = tk.StringVar()
        self.search_result = tk.StringVar(value="搜索全文")
        self.history_query = tk.StringVar()
        self.audio_hint = tk.StringVar(value=AUDIO_IDLE_HINT)
        self.task_status = tk.StringVar()
        self.audio = AudioPreview()
        self._audio_document_id = None
        self._audio_segment_index = None
        self._editor_open = False
        self._settings_open = False
        self._edit_dirty = False
        self._discarded_edit = None
        self._full_title = ''
        self._import_requested = False
        self._speaker_keys = []

    def bind_workspace(self):
        self.search_query.trace_add("write", self.schedule_search)
        self.history_query.trace_add("write", lambda *_: self.filter_history())
        # Caps Lock changes the keysym, so bind both cases.
        for key in ("<Control-f>", "<Control-F>"):
            self.bind(key, self.focus_search)
        self.bind("<F3>", lambda _: self.search_next())
        self.bind("<Shift-F3>", lambda _: self.search_previous())
        self.bind("<Control-Return>", lambda _: self.toggle_audio())
        for key in ("<Control-e>", "<Control-E>"):
            self.bind(key, self.focus_editor)
        for key in ("<Control-z>", "<Control-Z>"):
            self.bind(key, self.restore_discarded_edit)
        self.search_entry.bind("<Return>", lambda _: self.search_next())
        self.search_entry.bind("<Shift-Return>", lambda _: self.search_previous())
        self.search_entry.bind("<Escape>", lambda _: self.search_query.set(""))
        self.audio_timer = self.after(200, self.poll_audio)
        self.update_editor_visibility()

    def toggle_editor(self):
        if self._editor_open:self.finish_segment_edit()
        else:self.open_segment_editor(self.selected)

    def focus_editor(self,_event=None):
        if not self.document_locked and self.selected is not None:
            self.import_drawer.hide()
            self.preferences_drawer.hide()
            if self._editor_open and self._edit_index == self.selected:
                # Already correcting this paragraph: keep the draft uncommitted.
                self.segment_editor.focus_set()
            else:
                self.open_segment_editor(self.selected)
        return 'break'

    def update_editor_visibility(self):
        if self._editor_open and self.selected is not None:
            self.reader.show_editor(self.editor_body,self.selected)
        else:
            self.reader.hide_editor()
            self.speaker_name_row.pack_forget()

    def toggle_settings(self):
        self._settings_open=not self.import_drawer.shown
        self._import_requested=self._settings_open
        self.import_flow.sync()

    def update_settings_visibility(self):
        self.import_flow.sync()

    def schedule_search(self, *_):
        if getattr(self, "search_timer", None):
            self.after_cancel(self.search_timer)
        self.search_timer = self.after(150, self.apply_search)

    def apply_search(self):
        if getattr(self, "search_timer", None):
            self.after_cancel(self.search_timer)
            self.search_timer = None
        # Typing a query while correcting only highlights; it never leaves
        # (and thereby commits) the paragraph being edited.
        return self.reader.search(self.search_query.get(), activate=not self._editor_open)

    def search_results_changed(self, current, total):
        self.search_result.set(f"{current} / {total} 处" if total else ("无匹配" if self.search_query.get() else "搜索全文"))
        for name in ("search_prev_button", "search_next_button"):
            if hasattr(self, name):
                getattr(self, name).configure(state="normal" if total else "disabled")

    def focus_search(self, _event=None):
        if not self.doc or not self.doc.get('segments'):
            return 'break'
        self._settings_open = False
        self.update_settings_visibility()
        self.preferences_drawer.hide()
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
        if self._editor_open:
            self._draft_speaker=key;self._edit_dirty=True
            return
        segment = self.doc["segments"][self.selected]
        if segment["speaker"] == key:
            return
        segment["speaker"] = key
        self.reader.update_segment(self.selected, segment, self.doc["speakers"])
        self.doc["summary_stale"] = bool(self.doc["summary"])
        self.select_segment(self.selected)
        self.schedule_save()

    def show_merge_menu(self):
        if self.document_locked or self.selected is None:
            return
        source = self.doc["segments"][self.selected]["speaker"]
        from ui_controls import RoundedMenu
        menu = RoundedMenu(self, anchor=self.merge_button, min_width=190)
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
        if not self.commit_segment_edit():return
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
        self.set_busy(self.busy)
        self.select_segment(self.selected)
        self.flush_edits()

    def rename_document(self):
        if not self.doc or self.document_locked:return
        self.rename_value.set(self.doc['title'])
        self.title_line.grid_remove();self.rename_area.grid(row=0,column=0,sticky='ew')
        self.rename_entry.focus_set();self.rename_entry.select_range(0,'end')

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
            from studio_icons import glyph
            self.play_button.configure(text="回听",image=glyph('play',21))

    def poll_audio(self):
        state = self.audio.poll()
        current = self.doc and self.doc["id"] == self._audio_document_id
        if current:
            labels = {"preparing": "准备中…", "playing": "暂停", "paused": "继续"}
            from studio_icons import glyph
            self.play_button.configure(text=labels.get(state["state"], "回听"), image=glyph("pause" if state["state"]=="playing" else "play",21))
            if state["state"] in ("playing", "paused", "finished"):
                self.update_audio_position(state["position_ms"])
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






    def jump_to_time(self, position_ms):
        if not self.doc or not self.doc["segments"]:
            return
        self._settings_open = False
        self.update_settings_visibility()
        candidates = [i for i, segment in enumerate(self.doc["segments"]) if segment["start_ms"] <= position_ms]
        index = candidates[-1] if candidates else 0
        self.tabs.set("文字稿")
        self.reader.select(index)

    def open_segment_editor(self,index=None):
        if self.document_locked or not self.doc or index is None:return
        if self._editor_open and not self.commit_segment_edit():return
        self.reader.select(index)
        self._editor_open=True
        self._edit_dirty=False
        self._edit_index=index
        self._draft_speaker=self.doc['segments'][index]['speaker']
        self._discarded_edit=None
        self.fit_editor_height()
        self.update_editor_visibility()
        self.segment_editor.focus_set()
        self.after_idle(self.fit_editor_height)

    def fit_editor_height(self):
        """Size the inline editor to its text (3-8 lines) instead of a fixed block."""
        if not self._editor_open:return
        from tkinter import font as tkfont
        import customtkinter as ctk
        editor=self.segment_editor
        scale=ctk.ScalingTracker.get_widget_scaling(self)
        face=tkfont.Font(root=editor,font=editor.cget('font'))
        line=face.metrics('linespace')+int(float(editor.cget('spacing2') or 0))
        lines=0
        if editor.winfo_ismapped() and editor.winfo_width()>40:
            counted=editor.count('1.0','end','displaylines')
            lines=counted[0] if isinstance(counted,tuple) else int(counted or 0)
        if not lines:
            width=max(120,self.reader.text.winfo_width()-round(190*scale))
            lines=sum(max(1,math.ceil(face.measure(row)/width)) for row in editor.get('1.0','end-1c').split('\n'))
        rows=max(3,min(8,lines))
        extra=44 if self.speaker_name_row.winfo_manager() else 0
        desired=round(56+extra+(rows*line+18)/scale+54)
        if getattr(self.editor_body,'_desired_height',None)!=desired:
            self.editor_body._desired_height=desired
            self.reader.update_layout()

    def commit_segment_edit(self):
        if not self._editor_open:return True
        index=self._edit_index
        value=self.segment_editor.get('1.0','end-1c').strip()
        if not value:
            self.status.set('这段文字为空，请输入内容或取消校对。')
            self.segment_editor.focus_set()
            return False
        self._editor_open=False
        self.reader.hide_editor()
        row=self.doc['segments'][index]
        changed=value!=row['text'] or self._draft_speaker!=row['speaker']
        row['text']=value;row['speaker']=self._draft_speaker
        if changed:
            self.doc['summary_stale']=bool(self.doc.get('summary'))
            self.reader.update_segment(index,row,self.doc['speakers'])
            self.save_status.set('保存中…')
        self._edit_dirty=False
        self.speaker_name_row.pack_forget()
        return True

    def finish_segment_edit(self):
        if self.commit_segment_edit():
            self.flush_edits()
            self.reader.text.focus_set()
        return 'break'

    def cancel_segment_edit(self):
        discarded=None
        if self._editor_open and self.doc and self._edit_index is not None and self._edit_index<len(self.doc['segments']):
            row=self.doc['segments'][self._edit_index]
            text=self.segment_editor.get('1.0','end-1c')
            if text.strip() and (text.strip()!=row['text'] or self._draft_speaker!=row['speaker']):
                discarded={'document':self.doc['id'],'index':self._edit_index,'text':text,'speaker':self._draft_speaker}
        self._editor_open=False;self._edit_dirty=False
        self.reader.hide_editor();self.speaker_name_row.pack_forget()
        self.select_segment(self.selected)
        self._discarded_edit=discarded
        self.save_status.set('已放弃本段修改 · Ctrl Z 恢复' if discarded else '已取消本次校对')
        if discarded:self.status.set('已放弃本段修改。按 Ctrl Z 可恢复刚才的内容继续校对。')
        self.reader.text.focus_set()
        return 'break'

    def restore_discarded_edit(self,_event=None):
        """Reopen the editor with the draft dropped by the last Esc/取消."""
        draft=self._discarded_edit
        if self._editor_open or not draft or self.document_locked or not self.doc:return
        if draft['document']!=self.doc['id'] or draft['index']>=len(self.doc['segments']):
            self._discarded_edit=None;return
        self.open_segment_editor(draft['index'])
        if not self._editor_open:return 'break'
        self.segment_editor.delete('1.0','end');self.segment_editor.insert('1.0',draft['text'])
        if draft['speaker'] in self.doc['speakers']:
            self._draft_speaker=draft['speaker']
            values=self.speaker_box.cget('values')
            if draft['speaker'] in self._speaker_keys:self.speaker_box.set(values[self._speaker_keys.index(draft['speaker'])])
        self._edit_dirty=True
        self.save_status.set('正在校对 · 保存后生效')
        self.fit_editor_height()
        return 'break'

    def show_preferences(self):
        self.check_models()
        self.preferences_drawer.show()

    def close_import_flow(self):
        self.import_drawer.hide()
        self._settings_open=False;self._import_requested=False
        previous=getattr(self,'_return_document',None)
        self._return_document=None
        if previous and not self.busy and (not self.doc or not self.doc.get('task_id')):
            self.load_document(previous)
        self.import_flow.sync()

    def save_document_name(self):
        if not self.doc or self.document_locked:return
        value=' '.join(self.rename_value.get().split())[:120]
        if not value:
            self.status.set('请输入文稿名称。');return
        self.doc['title']=value;self.title_text.set(value);self.import_name.set(value)
        self.cancel_document_name();self.import_flow.sync()
        if self.flush_edits():self.refresh_history()

    def cancel_document_name(self):
        self.rename_area.grid_remove();self.title_line.grid()

    def update_audio_position(self,position=None):
        from studio_icons import glyph
        if not self.doc or self.selected is None:return
        row=self.doc['segments'][self.selected]
        position=row['start_ms'] if position is None else position
        duration=self.doc.get('duration_ms',0)
        def clock(value):
            text=timestamp(value)
            return text[3:] if text.startswith('00:') else text
        self.audio_position.set(f'{clock(position)} / {clock(duration)}')
        count=len(self.doc['segments']);start=max(0,min(self.selected-1,count-4))
        for i,button in enumerate(self.segment_cues):
            index=start+i
            if index<count:
                button.grid()
                button.configure(text=clock(self.doc['segments'][index]['start_ms']),
                    fg_color='#355862' if index==self.selected else 'transparent',
                    text_color='#D5F3F5' if index==self.selected else '#A7BACC',
                    command=lambda n=index:self.reader.select(n))
            else:button.grid_remove()
        self.previous_segment_button.configure(state='normal' if self.selected>0 else 'disabled')
        self.next_segment_button.configure(state='normal' if self.selected<count-1 else 'disabled')

    def rename_speaker(self):
        if self.document_locked or self.selected is None:return
        speaker=getattr(self,'_draft_speaker',None) or self.doc['segments'][self.selected]['speaker']
        self.speaker_name_value.set(self.doc['speakers'][speaker])
        self.speaker_name_row.pack(fill='x',padx=12,pady=(2,6),before=self.segment_editor)
        self.fit_editor_height()
        self.speaker_name_entry.focus_set();self.speaker_name_entry.select_range(0,'end')

    def save_speaker_name(self):
        if self.document_locked or self.selected is None:return
        name=' '.join(self.speaker_name_value.get().split())[:60]
        if not name:return
        speaker=getattr(self,'_draft_speaker',None) or self.doc['segments'][self.selected]['speaker']
        index=self.selected
        # Renaming a speaker is saved on its own. The paragraph draft stays
        # uncommitted, so Esc can still discard it afterwards.
        editing=self._editor_open
        draft=self.segment_editor.get('1.0','end-1c') if editing else None
        dirty,draft_speaker=self._edit_dirty,getattr(self,'_draft_speaker',None)
        self._editor_open=False
        self.speaker_name_row.pack_forget()
        self.doc['speakers'][speaker]=name
        self.doc['summary_stale']=bool(self.doc.get('summary'))
        self.reader.set_document(self.doc['segments'],self.doc['speakers'],selected=index)
        self.select_segment(index);self.flush_edits()
        if not editing:return
        self.open_segment_editor(index)
        if not self._editor_open:return
        if draft!=self.segment_editor.get('1.0','end-1c'):
            self.segment_editor.delete('1.0','end');self.segment_editor.insert('1.0',draft)
        if draft_speaker in self.doc['speakers']:
            self._draft_speaker=draft_speaker
            values=self.speaker_box.cget('values')
            self.speaker_box.set(values[self._speaker_keys.index(draft_speaker)])
        self._edit_dirty=dirty
        if dirty:self.save_status.set('正在校对 · 保存后生效')
        self.fit_editor_height()

    def cancel_speaker_name(self,_event=None):
        self.speaker_name_row.pack_forget()
        self.fit_editor_height()
        self.segment_editor.focus_set()
        return 'break'

    def set_page_title(self,title):
        self._full_title=str(title or '')
        self.refit_page_title()

    def refit_page_title(self):
        """Keep the heading to two lines; the full name stays in the document."""
        from tkinter import font as tkfont
        from ui_theme import FONT
        title=self._full_title
        limit=max(200,int(self.heading_label.cget('wraplength') or 560))*1.8
        face=tkfont.Font(root=self,family=FONT,size=-20)
        if face.measure(title)>limit:
            low,high=1,len(title)
            while low<high:
                middle=(low+high+1)//2
                if face.measure(title[:middle]+'…')<=limit:low=middle
                else:high=middle-1
            title=title[:low].rstrip()+'…'
        if self.page_title.get()!=title:self.page_title.set(title)
