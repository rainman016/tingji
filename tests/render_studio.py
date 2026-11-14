"""Render only an isolated synthetic studio; no media inference or user documents."""
from pathlib import Path
import sys
import tempfile
import tkinter.font as tkfont
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from tests.render_workspace import capture


def main():
    target = Path(__file__).resolve().parents[1] / '.tmp'
    with tempfile.TemporaryDirectory() as folder, patch.object(app, 'RESULTS', Path(folder)), patch.object(app, 'local_status', return_value={'asr':True,'english':True,'language_detection':True}):
        window = app.App()
        window.attributes('-alpha', 0)
        failures=[]
        window.report_callback_exception=lambda typ,value,tb: failures.append(str(value))
        def render(name):
            for _ in range(5): window.update()
            capture(window, target / ('studio-'+name+'.png'))
            print(name,window.winfo_width(),window.winfo_height(),flush=True)
        try:
            window.open_example()
            window.flush_edits();window.refresh_history()
            render('workspace')
            print('reading_font',tkfont.Font(root=window,font=window.reader.text.cget('font')).actual(),flush=True)
            window.open_segment_editor(1)
            render('edit')
            window.cancel_segment_edit()
            window.toggle_settings()
            render('options')
            window.close_import_flow()
            window.new_transcription()
            render('import')
            source=Path(folder)/'产品讨论.mp4';source.touch()
            with patch.object(app.filedialog,'askopenfilename',return_value=str(source)):window.choose_file()
            render('confirm')
            window.close_import_flow()
            window.show_preferences()
            render('preferences')
            window.preferences_drawer.hide()
            window.geometry('960x680')
            render('compact')
            window.open_segment_editor(0)
            render('compact-edit')
            window.cancel_segment_edit();window.new_transcription()
            render('compact-import')
            if failures: raise AssertionError(failures)
        finally:
            window.destroy()


if __name__ == '__main__': main()
