"""Rounded desktop menus shared by selectors and document actions."""
from __future__ import annotations

import os
import tkinter as tk
import customtkinter as ctk

from ui_theme import ACCENT, FONT, INK, LINE, MUTED, PAPER, SOFT, UI_BODY


class RoundedMenu:
    """A short-lived, locally grabbed menu; never binds to other applications."""
    def __init__(self, master, *, anchor=None, selected=None, min_width=180):
        self.master, self.anchor = master, anchor
        self.selected, self.min_width = selected, min_width
        self.items = []
        self.window = None
        self.buttons = []
        self.active = 0
        self._return_focus = None

    def add_command(self, *, label, command):
        self.items.append((label, command))

    def index(self, value):
        return len(self.items) - 1 if self.items else None

    def invoke(self, index):
        self.close()
        self.items[int(index)][1]()

    def tk_popup(self, x, y):
        if not self.items:
            return
        if self.window is not None:
            self.close()
            return
        self._return_focus = self.master.focus_get()
        self._previous_grab = self.master.grab_current()
        top = tk.Toplevel(self.master)
        self.window = top
        top.withdraw()
        top.overrideredirect(True)
        top.transient(self.master.winfo_toplevel())
        key = '#010203'
        top.configure(bg=key if os.name == 'nt' else PAPER)
        if os.name == 'nt':
            top.attributes('-transparentcolor', key)
        shell = ctk.CTkFrame(top, corner_radius=12, fg_color=PAPER,
                             border_color=LINE, border_width=1)
        shell.pack(fill='both', expand=True)
        self.buttons = []
        for index, (label, _) in enumerate(self.items):
            chosen = label == self.selected
            item = ctk.CTkButton(shell, text=label + ('   ✓' if chosen else ''),
                anchor='w', height=38, corner_radius=7, font=(FONT, UI_BODY),
                fg_color=SOFT if chosen else PAPER, hover_color=SOFT,
                text_color=ACCENT if chosen else INK, command=lambda i=index: self.invoke(i))
            item.pack(fill='x', padx=6, pady=(6 if index == 0 else 1, 6 if index == len(self.items)-1 else 1))
            item.bind('<Enter>', lambda _, i=index: self._highlight(i))
            self.buttons.append(item)
        top.update_idletasks()
        width = max(top.winfo_reqwidth(), self.min_width)
        height = top.winfo_reqheight()
        left, upper, right, bottom = self._work_area(x, y)
        x = max(left + 4, min(x, right - width - 4))
        if y + height > bottom:
            y = (self.anchor.winfo_rooty() if self.anchor else y) - height - 5
        y = max(upper + 4, min(y, bottom - height - 4))
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.bind('<Escape>', lambda _: self.close())
        top.bind('<Up>', lambda _: self._move(-1))
        top.bind('<Down>', lambda _: self._move(1))
        top.bind('<Home>', lambda _: self._highlight(0))
        top.bind('<End>', lambda _: self._highlight(len(self.items)-1))
        top.bind('<Return>', lambda _: self.invoke(self.active))
        top.bind('<space>', lambda _: self.invoke(self.active))
        top.bind('<Tab>', lambda _: self._tab(1))
        top.bind('<Shift-Tab>', lambda _: self._tab(-1))
        top.bind('<ButtonPress-1>', self._outside, add='+')
        top.bind('<FocusOut>', lambda _: top.after_idle(self._focus_left), add='+')
        top.deiconify()
        top.lift()
        top.grab_set()
        top.focus_force()
        self.active = next((i for i, (label, _) in enumerate(self.items) if label == self.selected), 0)
        self._highlight(self.active)

    def _work_area(self, x, y):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                            ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
            user = ctypes.windll.user32
            user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user.MonitorFromPoint.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            info = MonitorInfo(); info.size = ctypes.sizeof(info)
            if user.GetMonitorInfoW(user.MonitorFromPoint(wintypes.POINT(x, y), 2), ctypes.byref(info)):
                r = info.work
                return r.left, r.top, r.right, r.bottom
        return 0, 0, self.master.winfo_screenwidth(), self.master.winfo_screenheight()

    def _highlight(self, index):
        self.active = index
        for i, item in enumerate(self.buttons):
            item.configure(fg_color=SOFT if i == index or self.items[i][0] == self.selected else PAPER,
                           text_color=ACCENT if i == index or self.items[i][0] == self.selected else INK)
        return 'break'

    def _move(self, step):
        return self._highlight((self.active + step) % len(self.items))

    def _outside(self, event):
        top = self.window
        if top and not (top.winfo_rootx() <= event.x_root < top.winfo_rootx()+top.winfo_width()
                        and top.winfo_rooty() <= event.y_root < top.winfo_rooty()+top.winfo_height()):
            self.close()
            return 'break'

    def _focus_left(self):
        if self.window:
            focused = self.window.focus_get()
            if focused is None or focused.winfo_toplevel() != self.window:
                self.close(return_focus=False)

    def _tab(self, direction):
        anchor = self._return_focus
        target = (anchor.tk_focusNext() if direction > 0 else anchor.tk_focusPrev()) if anchor else None
        self.close(return_focus=False)
        if target:
            target.focus_set()
        return 'break'

    def close(self, return_focus=True):
        top, self.window = self.window, None
        if top:
            try:
                top.grab_release()
                top.destroy()
            except tk.TclError:
                pass
        self.buttons = []
        if return_focus and top and self._return_focus:
            try:
                self._return_focus.focus_set()
            except tk.TclError:
                pass
        previous=getattr(self,'_previous_grab',None)
        if top and previous:
            try:
                if previous.winfo_viewable():previous.grab_set()
            except tk.TclError:
                pass
        return 'break'

    def grab_release(self):
        # tk_popup is asynchronous here; close releases its local grab.
        pass


class RoundedComboBox(ctk.CTkComboBox):
    """Retain CTk's value/state API and drawing; replace only its square menu."""
    def __init__(self, *args, **kwargs):
        self.popup = None
        super().__init__(*args, **kwargs)
        self._entry.configure(takefocus=1, cursor='hand2')
        for key in ('<Button-1>', '<Down>', '<Up>', '<Return>', '<space>', '<Alt-Down>'):
            self._entry.bind(key, self._open, add='+')
        self._entry.bind('<FocusIn>', lambda _: self.configure(border_color=ACCENT))
        self._entry.bind('<FocusOut>', lambda _: self.configure(border_color=LINE))

    def _open(self, _event=None):
        self._open_dropdown_menu()
        return 'break'

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._canvas.itemconfig('border_parts_right', fill=self._apply_appearance_mode(self._border_color),
                                outline=self._apply_appearance_mode(self._border_color))

    def _on_enter(self, event=None):
        super()._on_enter(event)
        self._draw()

    def _on_leave(self, event=None):
        super()._on_leave(event)
        self._draw()

    def _open_dropdown_menu(self):
        if self.cget('state') == 'disabled':
            return
        if self.popup and self.popup.window:
            self.popup.close()
            return
        self._entry.focus_set()
        self.popup = RoundedMenu(self, anchor=self, selected=self.get(), min_width=self.winfo_width())
        for value in self.cget('values'):
            self.popup.add_command(label=value, command=lambda v=value: self._dropdown_callback(v))
        self.popup.tk_popup(self.winfo_rootx(), self.winfo_rooty()+self.winfo_height()+5)

    def configure(self, require_redraw=False, **kwargs):
        if self.popup and ('values' in kwargs or kwargs.get('state') == 'disabled'):
            self.popup.close()
        return super().configure(require_redraw=require_redraw, **kwargs)

    def destroy(self):
        if self.popup:
            self.popup.close(return_focus=False)
        super().destroy()
