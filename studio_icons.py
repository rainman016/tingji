"""Small, local line glyphs for the transcript studio; no network assets.

Every glyph is drawn on a 24-unit grid with one stroke weight and round caps,
rendered large and scaled down so edges stay crisp at 16-25 px. The brand mark
is the only filled shape: a rounded tile holding five voice bars.
"""
from functools import lru_cache

from PIL import Image, ImageDraw
import customtkinter as ctk

STROKE = 1.8
FILE_SHAPES = ('video', 'audio', 'document')


@lru_cache(maxsize=160)
def raster(name, ink='#3E7380', fill='#DCEFF1', detail='#087F8C', pixels=192):
    image = Image.new('RGBA', (pixels, pixels))
    draw = ImageDraw.Draw(image)
    scale = pixels / 24

    def box(points):
        return tuple(round(v * scale) for v in points)

    def line(points, color=ink, width=STROKE):
        pts = [(round(x * scale), round(y * scale)) for x, y in points]
        draw.line(pts, fill=color, width=max(1, round(width * scale)), joint='curve')
        r = width * scale / 2
        for x, y in (pts[0], pts[-1]):
            draw.ellipse((x-r, y-r, x+r, y+r), fill=color)

    def rect(coords, color=ink, radius=1):
        draw.rounded_rectangle(box(coords), radius=round(radius * scale), fill=color)

    def ring(center, radius, color=ink, width=STROKE):
        x, y = center
        draw.ellipse(box((x-radius, y-radius, x+radius, y+radius)), outline=color, width=round(width * scale))

    def dot(center, radius, color=ink):
        x, y = center
        draw.ellipse(box((x-radius, y-radius, x+radius, y+radius)), fill=color)

    def polygon(points, color=ink):
        draw.polygon([(x*scale, y*scale) for x, y in points], fill=color)

    if name == 'voice':
        # Brand mark: tile in `fill`, bars in `detail`.
        rect((1, 1, 23, 23), fill, 6.5)
        for x, height in ((6, 5), (9, 11), (12, 7), (15, 13), (18, 5)):
            line([(x, 12-height/2), (x, 12+height/2)], color=detail, width=1.9)
    elif name == 'voice-small':
        # Same mark for 16-24 px application icons: three heavier bars.
        rect((1, 1, 23, 23), fill, 6)
        for x, height in ((7, 6), (12, 13), (17, 6)):
            line([(x, 12-height/2), (x, 12+height/2)], color=detail, width=3)
    elif name == 'import':
        line([(12, 3.5), (12, 14.5)])
        line([(7.5, 10.5), (12, 15), (16.5, 10.5)])
        line([(4.5, 16.5), (4.5, 20), (19.5, 20), (19.5, 16.5)])
    elif name == 'export':
        line([(12, 5), (5, 5), (5, 19), (19, 19), (19, 12)])
        line([(11, 13), (20, 4)])
        line([(14.5, 4), (20, 4), (20, 9.5)])
    elif name == 'edit':
        line([(4, 20), (5, 15.5), (15.5, 5), (19, 8.5), (8.5, 19), (4, 20)])
        line([(13.5, 7), (17, 10.5)], width=1.4)
    elif name == 'search':
        ring((10.5, 10.5), 6.5)
        line([(15.6, 15.6), (20.5, 20.5)])
    elif name == 'play':
        polygon([(8, 5), (19.5, 12), (8, 19)])
        line([(8, 5), (19.5, 12), (8, 19), (8, 5)], width=1.2)
    elif name == 'pause':
        rect((7, 5, 10.5, 19), ink, 1.2)
        rect((13.5, 5, 17, 19), ink, 1.2)
    elif name in ('previous', 'next'):
        if name == 'previous':
            line([(5, 5), (5, 19)]); line([(19, 5), (9, 12), (19, 19), (19, 5)])
        else:
            line([(19, 5), (19, 19)]); line([(5, 5), (15, 12), (5, 19), (5, 5)])
    elif name in FILE_SHAPES:
        line([(6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)], width=1.6)
        line([(14, 3), (14, 8), (19, 8)], width=1.3)
        if name == 'video':
            polygon([(10.5, 11.5), (15.5, 14.5), (10.5, 17.5)])
        elif name == 'audio':
            line([(14, 11), (14, 16.5)], width=1.4)
            dot((12.2, 16.8), 1.9)
        else:
            line([(9.5, 12.5), (15.5, 12.5)], width=1.3)
            line([(9.5, 16.5), (14, 16.5)], width=1.3)
    elif name == 'settings':
        for y, x in ((6, 9), (12, 15.5), (18, 8)):
            line([(3.5, y), (20.5, y)], width=1.5)
            dot((x, y), 2.6)
    elif name == 'close':
        line([(6, 6), (18, 18)]); line([(18, 6), (6, 18)])
    elif name == 'check':
        line([(4, 12), (10, 18), (21, 5)], width=2)
    elif name == 'folder':
        line([(3, 6.5), (9, 6.5), (11.5, 9.5), (21, 9.5), (21, 19.5), (3, 19.5), (3, 6.5)], width=1.6)
    elif name == 'link':
        draw.rounded_rectangle(box((3, 5, 14, 13)), radius=3*scale, outline=ink, width=round(1.6*scale))
        draw.rounded_rectangle(box((10, 11, 21, 19)), radius=3*scale, outline=ink, width=round(1.6*scale))
    elif name in ('up', 'down'):
        line([(6, 15 if name == 'up' else 9), (12, 9 if name == 'up' else 15), (18, 15 if name == 'up' else 9)])
    return image


def write_app_icon(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Write the window/taskbar icon from the brand mark.

    Run ``python studio_icons.py`` to regenerate ``assets/tingji-studio.ico``.
    A teal tile with white bars stays legible on light and dark taskbars.
    """
    frames = []
    for size in sizes:
        mark = raster('voice-small' if size <= 24 else 'voice', '#FFFFFF', '#087F8C', '#FFFFFF', pixels=size * 8)
        frames.append(mark.resize((size, size), Image.LANCZOS))
    largest = frames[-1]
    largest.save(path, format='ICO', sizes=[(size, size) for size in sizes], append_images=frames[:-1])
    return frames


def glyph(name, size=22, *, dark=False, primary=False):
    ink, fill, detail = ('#C9DDE4', '#25434D', '#A6E3E7') if dark else ('#3E7380', '#DCEFF1', '#087F8C')
    if primary:
        ink, fill, detail = '#FFFFFF', '#3F9FA8', '#FFFFFF'
    picture = raster(name, ink, fill, detail)
    return ctk.CTkImage(light_image=picture, dark_image=picture, size=(size, size))


if __name__ == '__main__':
    from pathlib import Path
    target = Path(__file__).resolve().parent / 'assets' / 'tingji-studio.ico'
    write_app_icon(target)
    print('written', target)
