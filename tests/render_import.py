"""Manual visual acceptance: synthetic paths, isolated storage, no inference."""
import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_workspace import capture
import app
from core import new_document

if __name__=='__main__':
    out=Path(__file__).resolve().parents[1]/'.tmp'/'ui-polish-check'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, patch.object(app,'RESULTS',Path(tmp)):
        w=app.App();w.attributes('-alpha',0)
        try:
            for size,name in [('1240x820','wide'),('960x680','narrow')]:
                w.new_transcription();w.geometry(size)
                for _ in range(4):w.update()
                capture(w,out/f'upload-{name}.png')
                w.load_document(new_document('产品策略讨论.mp4'))
                for _ in range(4):w.update()
                capture(w,out/f'options-{name}.png')
                w.language_box._open_dropdown_menu();w.update()
                capture(w.language_box.popup.window,out/f'language-menu-{name}.png')
                w.language_box.popup.close()
                w.import_flow.toggle_advanced();w.update()
                start=w.start_button
                w.import_flow.reveal_widget(start);w.update()
                assert start.winfo_rooty()+start.winfo_height() <= w.winfo_rooty()+w.winfo_height(), 'Start below window'
                capture(w,out/f'options-more-{name}.png')
                if name=='narrow':
                    w.show_error('文件无法读取。请重新选择音视频文件后重试。')
                    w.update()
                    w.import_flow.reveal_widget(start);w.update()
                    capture(w,out/'options-error-narrow.png')
                    viewport=w.import_flow._parent_canvas
                    visible_bottom=viewport.winfo_rooty()+viewport.winfo_height()
                    assert start.winfo_rooty()+start.winfo_height() <= visible_bottom, 'Error blocks actions'
                    print('error view action visible: True')
                    w.clear_error()
                w.import_flow.toggle_advanced()
                w.worker=Mock();w._job_document_id=w.doc['id'];w.set_busy(True)
                w.show_progress('已完成识别片段 2 / 8，正在处理下一段。')
                doc=copy.deepcopy(w.doc);doc.update(language='en',status='本地识别中')
                w.load_document(doc)
                for _ in range(4):w.update()
                capture(w,out/f'processing-{name}.png')
                w.worker=None;w.set_busy(False)
                print(name,'import screens captured')
        finally:
            w.worker=None;w.destroy()
