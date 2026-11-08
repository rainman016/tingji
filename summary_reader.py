"""Read-only summary presentation; the original Markdown remains editable."""
import re
import tkinter as tk
import customtkinter as ctk

from ui_theme import FONT, PAPER, INK, MUTED, ACCENT, SOFT, SUMMARY_FONT


def blocks(source):
    """Recognize the document structure emitted by our summary templates."""
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        heading = re.match(r'^#{1,6}\s+(.+)', line)
        point = re.match(r'^(?:[-*+]\s+|\d+[.)、]\s+)(.+)', line)
        if heading:
            yield 'heading', heading[1]
        elif line.startswith('> '):
            yield 'quote', line[2:]
        elif re.match(r'^\[\d{2}:\d{2}:\d{2}\]', line):
            yield 'quote', line
        elif point:
            yield 'point', '•  ' + point[1]
        else:
            yield 'paragraph', line


class SummaryReader(tk.Frame):
    def __init__(self, parent, on_citation):
        super().__init__(parent, bg=PAPER)
        self.on_citation = on_citation
        self._signature = None
        self._identity = None
        self.text = tk.Text(self, wrap='word', state='disabled', borderwidth=0,
            highlightthickness=0, bg=PAPER, fg=INK, font=SUMMARY_FONT,
            padx=20, pady=12, spacing2=5, spacing3=12, cursor='xterm',
            selectbackground='#D8E9DD', selectforeground=INK, takefocus=True,
            width=1, height=1)
        self.text.pack(side='left', fill='both', expand=True)
        self.scrollbar = ctk.CTkScrollbar(self, command=self.text.yview, width=12,
            fg_color=PAPER, button_color='#D2DDD5', button_hover_color='#AFBFB3')
        self.scrollbar.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self.text.tag_configure('heading', font=(FONT, 15, 'bold'), foreground=INK,
            spacing1=16, spacing3=10)
        self.text.tag_configure('point', lmargin1=0, lmargin2=20, spacing3=14)
        self.text.tag_configure('quote', foreground=MUTED, background=SOFT,
            lmargin1=12, lmargin2=12, rmargin=8, spacing1=8, spacing3=14)
        self.text.tag_configure('strong', font=(FONT, 13, 'bold'))
        self.text.tag_configure('cjk', wrap='char')
        self.text.tag_configure('first', spacing1=0)
        self.text.tag_configure('citation', foreground=ACCENT, underline=True)
        self.text.tag_bind('citation', '<Button-1>', self._follow)
        self.text.tag_bind('citation', '<Enter>', lambda _: self.text.configure(cursor='hand2'))
        self.text.tag_bind('citation', '<Leave>', lambda _: self.text.configure(cursor='xterm'))
        self.text.bind('<Alt-Down>', lambda _: self._move_citation(1))
        self.text.bind('<Alt-Up>', lambda _: self._move_citation(-1))
        self.text.bind('<Return>', lambda _: self._follow_at(self.text.index('insert')))
        # Let the window's playback shortcut handle Ctrl+Enter.
        self.text.bind('<Control-Return>', lambda _: None)
        self.text.bind('<Control-a>', self._select_all)

    def _select_all(self, _event):
        self.text.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def _move_citation(self, direction):
        starts = self.text.tag_ranges('citation')[::2]
        if not starts:
            return 'break'
        position = self.text.index('insert')
        choices = [start for start in starts if self.text.compare(start, '>' if direction > 0 else '<', position)]
        start = (choices[0] if direction > 0 else choices[-1]) if choices else (starts[0] if direction > 0 else starts[-1])
        self.text.mark_set('insert', start)
        self.text.tag_remove('sel', '1.0', 'end')
        self.text.tag_add('sel', start, f'{start}+10c')
        self.text.see(start)
        return 'break'

    def _follow(self, event):
        return self._follow_at(self.text.index(f'@{event.x},{event.y}'))

    def _follow_at(self, position):
        ranges = self.text.tag_ranges('citation')
        for start, end in zip(ranges[::2], ranges[1::2]):
            if self.text.compare(start, '<=', position) and self.text.compare(position, '<', end):
                stamp = self.text.get(start, end).strip('[]')
                seconds = sum(int(value) * factor for value, factor in zip(stamp.split(':'), (3600, 60, 1)))
                self.on_citation(seconds * 1000)
                return 'break'

    def render(self, source, allowed_times, identity=None):
        signature = (source, frozenset(allowed_times), identity)
        if signature == self._signature:
            return
        fraction = self.text.yview()[0] if identity == self._identity else 0
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for kind, content in blocks(source):
            start = self.text.index('end-1c')
            cursor = 0
            for match in re.finditer(r'\*\*(.+?)\*\*', content):
                self.text.insert('end', content[cursor:match.start()], kind)
                self.text.insert('end', match[1], (kind, 'strong'))
                cursor = match.end()
            self.text.insert('end', content[cursor:] + '\n', kind)
            self.text.tag_add(kind, start, 'end-1c')
            if len(re.findall(r'[\u3400-\u9fff]', content)) > len(content) * .2:
                self.text.tag_add('cjk', start, 'end-1c')
        self.text.tag_add('first', '1.0', '1.end')
        position = '1.0'
        while True:
            match = self.text.search(r'\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]', position, stopindex='end', regexp=True)
            if not match:
                break
            position = f'{match}+10c'
            if self.text.get(match, position).strip('[]') in allowed_times:
                self.text.tag_add('citation', match, position)
        self.text.configure(state='disabled')
        self.text.yview_moveto(fraction)
        self._signature, self._identity = signature, identity
