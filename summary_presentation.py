"""Readable native text styling without changing or hiding editable content."""
import re
from ui_theme import FONT, ACCENT, MUTED, SOFT


def format_summary(editor):
    editor.tag_configure('summary_heading', font=(FONT, 13, 'bold'),
                         foreground=ACCENT, spacing1=14, spacing3=8)
    editor.tag_configure('summary_point', lmargin1=6, lmargin2=18, spacing1=3, spacing3=6)
    editor.tag_configure('summary_quote', foreground=MUTED, background=SOFT,
                         lmargin1=8, lmargin2=8, rmargin=8, spacing1=8, spacing3=8)
    for tag in ('summary_heading', 'summary_point', 'summary_quote'):
        editor.tag_remove(tag, '1.0', 'end')
    for line, text in enumerate(editor.get('1.0', 'end-1c').splitlines(), 1):
        stripped = text.strip()
        if stripped.startswith('#') or (len(stripped) < 32 and stripped.rstrip('*').endswith(('：', ':'))):
            tag = 'summary_heading'
        elif re.match(r'^(?:[-*]\s*)?\[\d{2}:\d{2}:\d{2}\]', stripped):
            tag = 'summary_quote'
        elif re.match(r'^(?:[-*]|\d+[.)、])\s', stripped):
            tag = 'summary_point'
        else:
            continue
        editor.tag_add(tag, f'{line}.0', f'{line}.end')
