import copy
import tempfile
import tkinter.font as tkfont
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
from core import new_document
from ui_theme import native_font


class ApprovedDesktopExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(app, 'RESULTS', Path(self.temp.name)); self.storage.start()
        self.models = patch.object(app, 'local_status', return_value={
            'asr': True, 'english': True, 'summary': True, 'language_detection': True})
        self.models.start()
        self.window = app.App()
        self.window.attributes('-alpha', 0)
        self.window.update()

    def tearDown(self):
        self.window.worker = None
        self.window.destroy()
        self.models.stop(); self.storage.stop(); self.temp.cleanup()

    def select_file(self):
        source = Path(self.temp.name) / '英文讨论.mp4'; source.touch()
        with patch.object(app.filedialog, 'askopenfilename', return_value=str(source)):
            self.window.choose_file()
        self.window.update()

    def test_import_options_follow_real_selection_and_cancel_preserves_document(self):
        w = self.window
        w.new_transcription();w.update()
        self.assertTrue(w.import_flow.upload.winfo_ismapped())
        self.assertTrue(w.tabs.winfo_ismapped())
        self.select_file()
        self.assertTrue(w.source_panel.winfo_ismapped())
        self.assertFalse(w.import_flow.upload.winfo_ismapped())
        self.assertEqual(w.language.get(), '自动检测')
        identity = w.doc['id']
        with patch.object(app.filedialog, 'askopenfilename', return_value=''):
            w.choose_file()
        self.assertEqual(w.doc['id'], identity)

    def test_custom_name_is_passed_to_real_start_without_mutating_source(self):
        self.select_file(); w = self.window
        source = w.doc['source']
        w.import_name.set('  访谈整理  ')
        with patch.object(w, 'launch_job') as launch:
            w.start()
        launch.assert_called_once_with()
        self.assertEqual(w.doc['title'], '访谈整理')
        self.assertEqual(w.doc['source'], source)

    def test_busy_stage_hides_options_then_delivers_original_during_summary(self):
        self.select_file(); w = self.window
        w._job_document_id = w.doc['id']; w.worker = Mock()
        w.set_busy(True); w.update()
        self.assertTrue(w.import_flow.processing.winfo_ismapped())
        self.assertFalse(w.source_panel.winfo_ismapped())
        self.assertEqual(w.import_name_entry.cget('state'), 'disabled')
        completed = copy.deepcopy(w.doc)
        completed.update(status='生成摘要', language='en', speakers={'0':'说话人 1'},
            segments=[{'start_ms':0,'end_ms':1000,'speaker':'0','text':'Original evidence.'}])
        w.load_document(completed); w.update()
        self.assertTrue(w.tabs.winfo_ismapped())
        self.assertFalse(w.import_drawer.shown)
        self.assertFalse(w.import_flow.winfo_viewable())
        self.assertEqual(w.segment_editor.cget('state'), 'disabled')
        self.assertEqual(w.export_button.cget('state'), 'normal')

    def test_language_confirmation_is_inline_instead_of_failure_panel(self):
        self.select_file(); w=self.window
        doc=copy.deepcopy(w.doc)
        doc.update(status='等待确认语言', error='需要手动确认语言',
                   language_detection={'needs_confirmation':True})
        w.load_document(doc); w.update()
        self.assertEqual(w.import_drawer.title.cget('text'),'请确认音频语言')
        self.assertTrue(w.language_box.winfo_ismapped())
        self.assertFalse(w.error_panel.winfo_ismapped())

    def test_new_import_saves_corrections_before_leaving_workspace(self):
        w=self.window; w.open_example()
        w.doc['segments'][0]['text']='需要保存的校对'
        with patch.object(w, 'flush_edits', return_value=False):
            w.new_transcription()
        self.assertIsNotNone(w.doc)
        self.assertEqual(w.doc['segments'][0]['text'], '需要保存的校对')
        w.new_transcription();w.update()
        self.assertIsNone(w.doc)
        self.assertTrue(w.import_flow.upload.winfo_ismapped())

    def test_standard_14px_and_large_16px_preserve_content(self):
        w=self.window;w.open_example();w.update()
        text=w.reader.text.get('1.0','end')
        self.assertFalse(hasattr(w,'summary_reader'))
        self.assertEqual(w.reader.text._reading_size,14)
        w.toggle_reading_size()
        self.assertEqual(w.reader.text._reading_size,16)
        self.assertEqual(w.reader.text.get('1.0','end'),text)
        w.toggle_reading_size()
        self.assertEqual(w.reader.text.cget('font'),w.segment_editor.cget('font'))
        self.assertEqual(w.reader.text._reading_size,14)

    def test_menu_keyboard_commit_escape_and_disabled_cleanup(self):
        self.select_file(); box=self.window.language_box
        box._open_dropdown_menu();self.window.update()
        menu=box.popup
        self.assertIsNotNone(menu.window)
        menu.window.event_generate('<Down>');menu.window.event_generate('<Return>')
        self.window.update()
        self.assertEqual(self.window.language.get(),'英文')
        self.assertIsNone(menu.window)
        box._open_dropdown_menu();self.window.update()
        box.popup.window.event_generate('<Escape>');self.window.update()
        self.assertIsNone(box.popup.window)
        box._open_dropdown_menu();self.window.update()
        box.configure(state='disabled')
        self.assertIsNone(box.popup.window)
        self.assertIs(self.window.grab_current(),self.window.import_drawer)

    def test_menu_outside_click_cancels_and_screen_edge_is_clamped(self):
        self.select_file(); box=self.window.language_box
        box._open_dropdown_menu();self.window.update();menu=box.popup
        menu._outside(SimpleNamespace(x_root=-1000,y_root=-1000))
        self.assertIsNone(menu.window)
        self.assertEqual(self.window.language.get(),'自动检测')
        x,y=self.window.winfo_screenwidth()-5,self.window.winfo_screenheight()-5
        menu.tk_popup(x,y);self.window.update();top=menu.window
        left,upper,right,bottom=menu._work_area(x,y)
        self.assertGreaterEqual(top.winfo_rootx(),left)
        self.assertLessEqual(top.winfo_rootx()+top.winfo_width(),right)
        self.assertLessEqual(top.winfo_rooty()+top.winfo_height(),bottom)
        menu.close()

    def test_settings_return_to_workspace_without_discarding_edits(self):
        w=self.window;w.open_example();w.update()
        w.toggle_settings();w.update()
        self.assertTrue(w.source_panel.winfo_ismapped())
        self.assertTrue(w.tabs.winfo_ismapped())
        self.assertTrue(w.import_drawer.shown)
        w.toggle_settings();w.update()
        self.assertTrue(w.tabs.winfo_ismapped())
        self.assertFalse(w.import_flow.winfo_ismapped())
        w.toggle_settings();w.focus_search();w.update()
        self.assertTrue(w.search_entry.winfo_ismapped())
        self.assertFalse(w.import_flow.winfo_ismapped())

    def test_narrow_error_and_more_options_keep_actions_reachable(self):
        self.select_file();w=self.window
        w.geometry('960x680');w.import_flow.toggle_advanced()
        w.show_error('文件无法读取，请重新选择。');w.update()
        w.import_flow.reveal_widget(w.start_button);w.update()
        viewport=w.import_flow._parent_canvas
        self.assertGreaterEqual(w.start_button.winfo_rooty(),viewport.winfo_rooty())
        self.assertLessEqual(w.start_button.winfo_rooty()+w.start_button.winfo_height(),
                            viewport.winfo_rooty()+viewport.winfo_height())
        self.assertTrue(w.error_panel.winfo_ismapped())

    def test_display_scaling_updates_native_readers_with_controls(self):
        import customtkinter as ctk
        w=self.window;w.open_example();w.update()
        before=tkfont.Font(root=w,font=w.reader.text.cget('font')).metrics('linespace')
        try:
            ctk.set_widget_scaling(1.25);w.update()
            after=tkfont.Font(root=w,font=w.reader.text.cget('font')).metrics('linespace')
            self.assertGreater(after,before)
            self.assertEqual(w.reader.text.cget('font'),w.segment_editor.cget('font'))
        finally:
            ctk.set_widget_scaling(1)


if __name__=='__main__':
    unittest.main()
