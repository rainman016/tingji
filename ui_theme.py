"""Shared reading-workbench colors and native typography."""
BG = '#FAFBFD'
PAPER = '#FFFFFF'
INK = '#18222E'
MUTED = '#637183'
ACCENT = '#087F8C'
HOVER = '#076B76'
LINE = '#E2E7EC'
SOFT = '#EDF6F7'
SIDE = '#171D26'
SIDE_TEXT = '#DCE4EC'
SIDE_MUTED = '#A1AEBD'
SIDE_ACTIVE = '#283442'
DOCK = '#202B37'
FONT = 'Microsoft YaHei'

# All sizes are logical pixels. Tk's negative font sizes opt into pixels;
# native_font also applies CTk's display scaling so both widget families match.
UI_BODY = 13
UI_CAPTION = 11
READING_FONT = (FONT, -14)
SUMMARY_FONT = (FONT, -14)  # Kept for standalone legacy document utilities.
READING_META = (FONT, -12)


def native_font(widget, size=14, weight='normal'):
    import customtkinter as ctk
    scale = ctk.ScalingTracker.get_widget_scaling(widget)
    return (FONT, -max(1, round(size * scale)), weight)
