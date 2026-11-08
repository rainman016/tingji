"""Manual, isolated visual check. Captures only its own synthetic-data window."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import struct
import tempfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def save_png(destination, width, height, pixels):
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = pixels[2::4], pixels[1::4], pixels[0::4]
    stride = width * 3
    rows = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    destination.write_bytes(b"\x89PNG\r\n\x1a\n" +
                            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def capture(window, destination):
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowDC.argtypes = [wintypes.HWND]
    user.GetWindowDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    hwnd = user.GetAncestor(window.winfo_id(), 2)
    rectangle = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rectangle))
    width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
    dc = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        class Header(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                        ("image_size", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                        ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
        header = Header(40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        save_png(destination, width, height, pixels.raw)
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / ".tmp"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workspace-render-") as folder, patch.object(app, "RESULTS", Path(folder)):
        window = app.App()
        window.attributes("-alpha", 0)
        try:
            window.open_example()
            window.doc["title"] = "产品例会 · 文字稿校对"
            window.load_document(window.doc)
            window.flush_edits()
            window.refresh_history()
            for label, size in (("wide", "1240x820"), ("narrow", "960x680")):
                window.geometry(size)
                for _ in range(5):
                    window.update()
                capture(window, destination / ("workspace-" + label + ".png"))
                print(label, "wide_mode=", window.tabs.wide_mode,
                      "reader_height=", window.reader.winfo_height(),
                      "window=", window.winfo_width(), "workspace=", window.tabs.winfo_width())
        finally:
            window.destroy()
