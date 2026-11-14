"""A compact, read-only transcript view backed by one Tk Text widget."""
from __future__ import annotations

from bisect import bisect_right
import re
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk


def _timestamp(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TranscriptReader(tk.Frame):
    """Render paragraphs without creating a widget for each paragraph.

    ``set_document`` and ``update_segment`` are view refreshes and do not call
    ``on_select``. User selection and search navigation do. Search covers the
    paragraph bodies, excluding the displayed timestamps and speaker labels.
    ``on_search_change(ordinal, total)`` may be supplied at construction or set
    as an attribute later. Ordinals are one-based, or zero when no match exists.
    """

    def __init__(self, parent, on_select=None, on_play=None, **styleargs):
        background = styleargs.pop("background", styleargs.pop("bg", "#FFFFFF"))
        foreground = styleargs.pop("foreground", styleargs.pop("fg", "#202C3D"))
        muted = styleargs.pop("muted_color", "#627186")
        accent = styleargs.pop("accent_color", "#1F5AE8")
        font = styleargs.pop("font", ("Microsoft YaHei UI", 11))
        metadata_font = styleargs.pop("metadata_font", ("Microsoft YaHei UI", 9))
        selected_background = styleargs.pop("selected_background", "#EDF3FF")
        match_background = styleargs.pop("match_background", "#FFF0B3")
        active_match_background = styleargs.pop("active_match_background", "#E9CD71")
        self.on_search_change = styleargs.pop("on_search_change", None)
        self.on_edit = styleargs.pop('on_edit', None)
        super().__init__(parent, background=background, **styleargs)
        self.on_select = on_select
        self.on_play = on_play
        self._segments = []
        self._speakers = {}
        self._start_lines = []
        self._selected = None
        self._query = ""
        self._matches = []
        self._current_match = None
        self._editor = None

        self.text = tk.Text(
            self, wrap="word", state="disabled", undo=False,
            borderwidth=0, highlightthickness=0, relief="flat",
            background=background, foreground=foreground, font=font,
            padx=18, pady=12, spacing2=5, spacing3=8, cursor="xterm", takefocus=True,
            selectbackground="#DCE8FF", selectforeground=foreground,
            insertbackground=accent,
        )
        self.scrollbar = ctk.CTkScrollbar(self, orientation='vertical', command=self.text.yview,
            width=10, fg_color=background, button_color='#D0DCE3', button_hover_color='#9EB7C2')
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.text.tag_configure("metadata", foreground=muted, font=metadata_font, spacing1=8, spacing3=3)
        # Only the selected paragraph's timestamp carries the accent colour.
        self.text.tag_configure("timestamp", foreground="#708093", underline=False)
        self.text.tag_configure("current_timestamp", foreground=accent)
        self.text.tag_configure('edit_action', foreground=muted)
        # The separator line between paragraphs is kept (indices rely on it)
        # but rendered as a thin, unhighlighted gap instead of a full row.
        self.text.tag_configure('gap', font=(metadata_font[0], -4), spacing1=0, spacing2=0, spacing3=0)
        self.text.tag_configure('body')
        self.text.tag_configure('editing_body', elide=True)
        self.text.tag_configure("cjk", wrap="char")
        self.text.tag_configure("current_segment", background=selected_background)
        self.text.tag_configure("search_match", background=match_background)
        self.text.tag_configure("current_match", background=active_match_background)
        self.text.tag_raise("sel")
        self.text.bind("<Button-1>", self._on_click)
        self.text.tag_bind("timestamp", "<Enter>", lambda _event: self.text.configure(cursor="hand2"))
        self.text.tag_bind("timestamp", "<Leave>", lambda _event: self.text.configure(cursor="xterm"))
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-A>", self._select_all)
        self.text.bind("<Control-c>", self._copy_selection)
        self.text.bind("<Control-C>", self._copy_selection)
        self.text.bind("<Alt-Up>", lambda _event: self._move_selection(-1))
        self.text.bind("<Alt-Down>", lambda _event: self._move_selection(1))
        self.text.bind("<Alt-Return>", self._play_selected)
        self.text.bind('<Configure>',lambda _:self.update_layout())
        for tag in ('timestamp','edit_action'):
            self.text.tag_bind(tag,'<Enter>',lambda _:self.text.configure(cursor='hand2'))
            self.text.tag_bind(tag,'<Leave>',lambda _:self.text.configure(cursor='xterm'))

    def update_layout(self):
        scale=ctk.ScalingTracker.get_widget_scaling(self)
        gutter=round(88*scale)
        width=self.text.winfo_width()
        self.text.tag_configure('body',lmargin1=gutter,lmargin2=gutter)
        self.text.tag_configure('metadata',tabs=(gutter,'left',max(gutter+120,width-round(55*scale)),'right'))
        if self._editor:
            # Height follows the text being corrected, capped so the
            # paragraph heading above it stays visible in small windows.
            desired=getattr(self._editor,'_desired_height',205)
            room=max(150,(self.text.winfo_height()-round(56*scale))/scale)
            self._editor.configure(width=max(320,(width-gutter-round(45*scale))/scale),
                height=max(150,min(desired,room)))

    def show_editor(self, widget, index):
        self.hide_editor()
        self._editor=widget
        self.update_layout()
        self.text.configure(state='normal')
        try:
            position=self.text.index(self._body_mark(index))
            self.text.mark_set('inline_editor',position)
            self.text.mark_gravity('inline_editor','left')
            self.text.window_create(position,window=widget,align='top',stretch=False,pady=4)
            self.text.tag_add('body','inline_editor','inline_editor+1c')
            self.text.tag_add('editing_body',self._body_mark(index),f'{self._end_index(index)}-2c')
            self.text.see('inline_editor')
        finally:self.text.configure(state='disabled')
        self.after_idle(self._reveal_editor,index)

    def _reveal_editor(self,index):
        # Runs once the embedded editor has its real size: show all of it,
        # then make sure the paragraph's timestamp/speaker line is not cut.
        if self._editor and 'inline_editor' in self.text.mark_names() and index<self.segment_count:
            self.text.see('inline_editor')
            self.text.see(self._start_mark(index))

    def hide_editor(self):
        if self._editor:
            self.text.configure(state='normal')
            try:
                if 'inline_editor' in self.text.mark_names():
                    self.text.window_configure('inline_editor',window='')
                    self.text.delete('inline_editor','inline_editor+1c')
                    self.text.mark_unset('inline_editor')
                self.text.tag_remove('editing_body','1.0','end')
            finally:self.text.configure(state='disabled')
            self._editor=None

    @property
    def selected_index(self):
        return self._selected

    @property
    def segment_count(self):
        return len(self._segments)

    @staticmethod
    def _start_mark(index):
        return f"transcript_start_{index}"

    @staticmethod
    def _body_mark(index):
        return f"transcript_body_{index}"

    def _end_index(self, index):
        if index + 1 < self.segment_count:
            return self.text.index(self._start_mark(index + 1))
        return self.text.index("end-1c")

    def _block(self, segment):
        speaker = self._speakers.get(segment.get("speaker"), "未区分说话人")
        stamp = _timestamp(segment.get("start_ms", 0))
        body = str(segment.get("text", ""))
        # Speaker names are metadata on one line, even in imported documents.
        speaker = " ".join(str(speaker).splitlines())
        return f"{stamp}\t{speaker}\t校对\n{body}\n\n"

    def _mark_segment(self, index, start_line):
        start = f"{start_line}.0"
        body = f"{start_line + 1}.0"
        self.text.mark_set(self._start_mark(index), start)
        self.text.mark_set(self._body_mark(index), body)
        # Right gravity moves following paragraphs past an inserted replacement.
        self.text.mark_gravity(self._start_mark(index), "right")
        self.text.mark_gravity(self._body_mark(index), "right")
        self.text.tag_add("metadata", start, f"{body}-1c")
        self.text.tag_add("timestamp", start, f"{start}+{len(_timestamp(self._segments[index].get('start_ms', 0)))}c")
        self.text.tag_add('edit_action',f'{start_line}.end-2c',f'{start_line}.end')
        content = str(self._segments[index].get('text', ''))
        end = f"{start_line + content.count(chr(10)) + 1}.end"
        self.text.tag_add('body',body,end)
        self.text.tag_add('gap',f'{end}+1c',f'{end}+2c')
        self.text.tag_remove('cjk', start, f'{end}+1c')
        if len(re.findall(r'[\u3400-\u9fff]', content)) > len(content) * .2:
            self.text.tag_add('cjk', body, end)

    def _refresh_start_lines(self):
        line = 1
        self._start_lines = []
        for segment in self._segments:
            self._start_lines.append(line)
            line += str(segment.get("text", "")).count("\n") + 3

    def set_document(self, segments, speakers, selected=0):
        self.hide_editor()
        old_count = self.segment_count
        self._segments = [dict(segment) for segment in segments]
        self._speakers = dict(speakers)
        self._selected = None
        self._refresh_start_lines()
        self.text.configure(state="normal")
        try:
            for index in range(old_count):
                self.text.mark_unset(self._start_mark(index), self._body_mark(index))
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "".join(self._block(segment) for segment in self._segments))
            for index, line in enumerate(self._start_lines):
                self._mark_segment(index, line)
        finally:
            self.text.configure(state="disabled")
        if self._segments and selected is not None:
            self.select(max(0, min(int(selected), self.segment_count - 1)), notify=False)
        self._rebuild_search(activate=False)

    def select(self, index, scroll=True, notify=True):
        if index is None or not self._segments:
            self._selected = None
            self.text.tag_remove("current_segment", "1.0", "end")
            self.text.tag_remove("current_timestamp", "1.0", "end")
            return None
        index = int(index)
        if not 0 <= index < self.segment_count:
            return None
        self._selected = index
        self.text.tag_remove("current_segment", "1.0", "end")
        self.text.tag_remove("current_timestamp", "1.0", "end")
        # Stop before the separator line so the tint hugs the paragraph.
        self.text.tag_add("current_segment", self._start_mark(index), f"{self._end_index(index)}-1c")
        start = self.text.index(self._start_mark(index))
        self.text.tag_add("current_timestamp", start, f"{start}+{len(_timestamp(self._segments[index].get('start_ms', 0)))}c")
        self.text.mark_set("insert", self._body_mark(index))
        if scroll:
            self.text.see(self._start_mark(index))
        if notify and self.on_select is not None:
            self.on_select(index)
        return index

    def _segment_at(self, position):
        if not self._segments:
            return None
        line = int(self.text.index(position).split(".")[0])
        return max(0, min(bisect_right(self._start_lines, line) - 1, self.segment_count - 1))

    def update_segment(self, index, segment, speakers):
        index = int(index)
        if not 0 <= index < self.segment_count:
            raise IndexError("paragraph index is out of range")
        old_selected = self._selected
        old_match = self._current_match
        top = self.text.index("@0,0")
        top_segment = self._segment_at(top)
        top_offset = (self.text.count(self._start_mark(top_segment), top, "chars") or (0,))[0]
        self.text.mark_set("transcript_viewport", top)
        self.text.mark_gravity("transcript_viewport", "right")
        changed_speakers = {
            speaker for speaker in set(self._speakers) | set(speakers)
            if self._speakers.get(speaker) != speakers.get(speaker)
        }
        self._segments[index] = dict(segment)
        self._speakers = dict(speakers)
        affected = {index} | {
            number for number, item in enumerate(self._segments)
            if item.get("speaker") in changed_speakers
        }
        self.text.configure(state="normal")
        try:
            for number in sorted(affected, reverse=True):
                start = self.text.index(self._start_mark(number))
                end = self._end_index(number)
                self.text.delete(start, end)
                self.text.insert(start, self._block(self._segments[number]), ())
                self._mark_segment(number, int(start.split(".")[0]))
            self._refresh_start_lines()
            if top_segment in affected:
                available = (self.text.count(self._start_mark(top_segment), self._end_index(top_segment), "chars") or (0,))[0]
                self.text.mark_set("transcript_viewport", f"{self._start_mark(top_segment)}+{min(top_offset, max(0, available - 1))}c")
        finally:
            self.text.configure(state="disabled")
        self.select(old_selected, scroll=False, notify=False)
        self._rebuild_search(activate=False, preferred=old_match)
        self.text.yview("transcript_viewport")
        self.text.mark_unset("transcript_viewport")

    def search(self, query, activate=True):
        self._query = str(query or "")
        return self._rebuild_search(activate=activate)

    def _body_position(self, number, body, line_offsets, offset):
        relative_line = bisect_right(line_offsets, offset) - 1
        line = self._start_lines[number] + 1 + relative_line
        # Tk's column index uses Tcl's string units. On Windows Tcl 8.6 an
        # astral character occupies two units, unlike a Python string index.
        # Generate a native line.column directly; +Nc has different traversal
        # semantics and can shift ranges after non-BMP characters.
        column = self.text.tk.call("string", "length", body[line_offsets[relative_line]:offset])
        return f"{line}.{column}"

    def _rebuild_search(self, activate, preferred=None):
        self.text.tag_remove("search_match", "1.0", "end")
        self.text.tag_remove("current_match", "1.0", "end")
        self._matches = []
        self._current_match = None
        if self._query and self._segments:
            pattern = re.compile(re.escape(self._query), re.IGNORECASE)
            for number, segment in enumerate(self._segments):
                body = str(segment.get("text", ""))
                line_offsets = [0] + [match.end() for match in re.finditer("\n", body)]
                # Match the original text rather than a lower/casefold copy:
                # Unicode casing can change the length of such a copy.
                for match in pattern.finditer(body):
                    found = self._body_position(number, body, line_offsets, match.start())
                    end = self._body_position(number, body, line_offsets, match.end())
                    self._matches.append((found, end, number))
                    self.text.tag_add("search_match", found, end)
        if self._matches:
            self._current_match = min(preferred or 0, len(self._matches) - 1)
            start, end, number = self._matches[self._current_match]
            self.text.tag_add("current_match", start, end)
            if activate:
                self.select(number, scroll=False)
                self.text.see(start)
        return self._notify_search()

    def next_match(self, direction=1):
        if not self._matches:
            return self._notify_search()
        step = 1 if direction >= 0 else -1
        current = self._current_match if self._current_match is not None else (-1 if step > 0 else 0)
        self._current_match = (current + step) % len(self._matches)
        start, end, number = self._matches[self._current_match]
        self.text.tag_remove("current_match", "1.0", "end")
        self.text.tag_add("current_match", start, end)
        self.select(number, scroll=False)
        self.text.see(start)
        return self._notify_search()

    def _notify_search(self):
        ordinal = self._current_match + 1 if self._current_match is not None else 0
        result = (ordinal, len(self._matches))
        if self.on_search_change is not None:
            self.on_search_change(*result)
        return result

    def _on_click(self, event):
        self.text.focus_set()
        position = self.text.index(f"@{event.x},{event.y}")
        if not self._segments or self.text.compare(position, ">=", "end-1c"):
            return
        number = self._segment_at(position)
        is_timestamp = "timestamp" in self.text.tag_names(position)
        is_edit = 'edit_action' in self.text.tag_names(position)
        bounds = self.text.bbox(position) if is_timestamp or is_edit else None
        hit_timestamp = bool(bounds and bounds[0] <= event.x < bounds[0] + bounds[2]
                             and bounds[1] <= event.y < bounds[1] + bounds[3])
        self.select(number, scroll=False)
        if hit_timestamp and is_edit and self.on_edit is not None:
            self.on_edit(number)
            return 'break'
        if hit_timestamp and is_timestamp and self.on_play is not None:
            self.on_play(number)

    def _move_selection(self, direction):
        if self._segments:
            current = self._selected if self._selected is not None else 0
            self.select(max(0, min(current + direction, self.segment_count - 1)))
        return "break"

    def _play_selected(self, _event=None):
        if self._selected is not None and self.on_play is not None:
            self.on_play(self._selected)
        return "break"

    def _select_all(self, _event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy_selection(self, _event=None):
        try:
            selected = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.text.clipboard_clear()
        self.text.clipboard_append(selected)
        return "break"
