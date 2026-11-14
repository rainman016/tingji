"""Responsive reading/summary panes with a small tab-compatible interface."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from ui_theme import FONT, INK, MUTED, SOFT, LINE, PAPER, ACCENT, UI_BODY


class WorkspaceView(ctk.CTkFrame):
    def __init__(self, master, *, breakpoint=880, **kwargs):
        super().__init__(master, fg_color="transparent", width=1, height=260, **kwargs)
        self._breakpoint = breakpoint
        self._panes = {}
        self._current = ""
        self._wide = False
        self._focused = False
        self._layout_timer = None
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        navigation = ctk.CTkFrame(self, fg_color="transparent")
        navigation.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.navigation = ctk.CTkSegmentedButton(
            navigation, values=['原文', '对照阅读', '摘要'], command=self.select_mode, height=40,
            width=280, dynamic_resizing=False,
            font=(FONT, UI_BODY), corner_radius=10,
            fg_color="#E8EEF5", selected_color=ACCENT,
            selected_hover_color=ACCENT, unselected_color="#E8EEF5",
            unselected_hover_color="#DCE7F7", text_color=INK)
        self.navigation.pack(side="left")
        self.reading_label = ctk.CTkLabel(navigation, text='文稿工作区', font=(FONT, 15, 'bold'), text_color=INK)
        self.layout_hint = ctk.CTkLabel(
            navigation, text="", font=(FONT, 13),
            text_color="#627186", anchor="e")
        self.layout_hint.pack(side="right", padx=4)
        self.focus_button = ctk.CTkButton(navigation, text='专注阅读', width=96, height=36,
                                         fg_color='#E8EEF5', hover_color='#DCE7F7', text_color='#202C3D',
                                         command=self.toggle_focus)
        self.focus_button.tk.call(self.focus_button._w, 'configure', '-takefocus', 1)
        self.focus_button.bind('<Return>', lambda _: self.toggle_focus())
        self.focus_button.bind('<space>', lambda _: self.toggle_focus())
        self.focus_button.bind('<FocusIn>', lambda _: self.focus_button.configure(border_width=2, border_color='#1F5AE8'))
        self.focus_button.bind('<FocusOut>', lambda _: self.focus_button.configure(border_width=0))
        self.content = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        # The outer grid owns this viewport's dimensions. Propagating the panes'
        # initial 1x1 requests can leave their first idle layout unmapped on Tk/Windows.
        self.content.grid_propagate(False)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        # CTkFrame.bind targets its internal canvas. Observe the actual frame so
        # Configure events and their widget identity describe this workspace.
        tk.Frame.bind(self, "<Configure>", self._on_workspace_resize, add="+")
        tk.Frame.bind(self.content, "<Configure>", self._on_workspace_resize, add="+")

    @property
    def wide_mode(self):
        return self._wide

    def add(self, name):
        if name in self._panes:
            raise ValueError(f"Duplicate workspace pane: {name}")
        pane = ctk.CTkFrame(self.content, fg_color=PAPER, corner_radius=14,
                           border_width=1, border_color="#DDE5EF", width=1, height=1)
        pane.pack_propagate(False)
        self._panes[name] = pane
        if not self._current:
            self._current = name
        self._wire_navigation()
        self._layout()
        return pane

    def _wire_navigation(self):
        # CustomTkinter's segments are frames; make their built-in buttons
        # reachable without a pointer and retain a visible keyboard focus.
        for name, item in self.navigation._buttons_dict.items():
            item.tk.call(item._w, "configure", "-takefocus", 1)
            item.bind("<Return>", lambda _event, key=name: self.select_mode(key))
            item.bind("<space>", lambda _event, key=name: self.select_mode(key))
            item.bind("<Right>", lambda _event, key=name: self._next_pane(key, 1))
            item.bind("<Left>", lambda _event, key=name: self._next_pane(key, -1))
            item.bind("<FocusIn>", lambda _event, button=item: button.configure(border_width=2, border_color="#91ADE4"))
            item.bind("<FocusOut>", lambda _event, button=item: button.configure(border_width=0))

    def _next_pane(self, current, direction):
        names = self.navigation.cget('values')
        name = names[(names.index(current) + direction) % len(names)]
        self.select_mode(name)
        self.navigation._buttons_dict[name].focus_set()
        return "break"

    def get(self):
        return self._current

    def select_mode(self, mode):
        if mode == '对照阅读':
            self._focused = False
        else:
            name = '文字稿' if mode == '原文' else '摘要与要点'
            if name not in self._panes:
                return
            self._current = name
            self._focused = True
        self._layout()

    def toggle_focus(self):
        self._focused = not self._focused
        self._layout()

    def set(self, name):
        if name not in self._panes:
            raise ValueError(f"Unknown workspace pane: {name}")
        self._current = name
        self._layout()

    def _on_workspace_resize(self, event):
        if event.widget not in (self, self.content):
            return
        if self._layout_timer is None:
            self._layout_timer = self.after_idle(self._layout)

    def _workspace_width(self):
        return self._reverse_widget_scaling(self.winfo_width())

    def _layout(self):
        self._layout_timer = None
        if not self._panes:
            return
        can_split = self._workspace_width() >= self._breakpoint and len(self._panes) > 1
        values = ['原文', '对照阅读', '摘要'] if can_split else ['原文', '摘要']
        if self.navigation.cget('values') != values:
            self.navigation.configure(values=values)
            self._wire_navigation()
        self.navigation.pack(side='left')
        self.reading_label.pack_forget()
        self._wide = can_split and not self._focused
        self.focus_button.configure(state='normal' if can_split else 'disabled',
                                    text='并排阅读' if self._focused else '专注阅读')
        for pane in self._panes.values():
            pane.grid_remove()
        if self._wide:
            self.content.grid_columnconfigure(0, weight=6, uniform="reading")
            self.content.grid_columnconfigure(1, weight=5, uniform="reading")
            for index, pane in enumerate(self._panes.values()):
                pane.grid(row=0, column=index, sticky="nsew", padx=(0, 12) if index == 0 else 0)
            self.layout_hint.configure(text="")
        else:
            self.reading_label.pack_forget()
            self.navigation.pack(side='left')
            self.content.grid_columnconfigure(0, weight=1, uniform="")
            self.content.grid_columnconfigure(1, weight=0, uniform="", minsize=0)
            self._panes[self._current].grid(row=0, column=0, sticky="nsew", padx=0)
            self.layout_hint.configure(text="")
        active = '对照阅读' if self._wide else '原文' if self._current == '文字稿' else '摘要'
        self.navigation.set(active)
        for name, item in self.navigation._buttons_dict.items():
            item.configure(text_color=PAPER if name == active else MUTED)

    def destroy(self):
        if self._layout_timer is not None:
            self.after_cancel(self._layout_timer)
            self._layout_timer = None
        super().destroy()
