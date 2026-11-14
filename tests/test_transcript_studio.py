import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import app
from core import export_text, new_document, save_document


class TranscriptStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.storage=patch.object(app,'RESULTS',Path(self.temp.name));self.storage.start()
        self.models=patch.object(app,'local_status',return_value={'asr':True,'english':True,'language_detection':True,'summary':False});self.models.start()
        self.errors=[]
        self.window=app.App();self.window.attributes('-alpha',0)
        self.window.report_callback_exception=lambda typ,value,tb:self.errors.append(str(value))
        self.window.open_example();self.window.update()

    def tearDown(self):
        self.window.worker=None
        self.window.destroy();self.models.stop();self.storage.stop();self.temp.cleanup()
        self.assertEqual(self.errors,[])

    def edit(self,text,index=0):
        w=self.window;w.open_segment_editor(index);w.update()
        w.segment_editor.delete('1.0','end');w.segment_editor.insert('1.0',text);w.update()

    def test_inline_save_cancel_and_reopen_keep_widgets_alive(self):
        w=self.window;original=w.doc['segments'][0]['text']
        self.edit('不保存的修改');w.cancel_segment_edit();w.update()
        self.assertEqual(w.doc['segments'][0]['text'],original)
        self.edit('确认保存的逐字稿');w.finish_segment_edit();w.update()
        saved=json.loads((Path(self.temp.name)/(w.doc['id']+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['segments'][0]['text'],'确认保存的逐字稿')
        self.edit('再次修改');w.cancel_segment_edit()
        self.assertTrue(w.speaker_box.winfo_exists())
        self.assertIn('确认保存的逐字稿',w.reader.text.get('1.0','end'))

    def test_empty_edit_blocks_leaving_until_cancelled(self):
        w=self.window;identity=w.doc['id'];self.edit('   ')
        w.new_transcription()
        self.assertEqual(w.doc['id'],identity);self.assertTrue(w._editor_open)
        with patch.object(app.filedialog,'asksaveasfilename') as choose:
            w.export('txt');choose.assert_not_called()
        w.reader.select(2)
        self.assertEqual(w.selected,0)
        w.cancel_segment_edit();w.new_transcription();w.update()
        self.assertTrue(w.import_drawer.shown)

    def test_selecting_current_paragraph_preserves_uncommitted_draft(self):
        w=self.window;original=w.doc['segments'][0]['text'];self.edit('尚未确认的校对')
        w.reader.select(0)
        self.assertEqual(w.segment_editor.get('1.0','end-1c'),'尚未确认的校对')
        self.assertEqual(w.doc['segments'][0]['text'],original)
        w.cancel_segment_edit()

    def test_changing_paragraph_commits_draft_and_search_updates(self):
        w=self.window;self.edit('独特检索词 evidence')
        w.reader.select(1);w.flush_edits()
        self.assertEqual(w.doc['segments'][0]['text'],'独特检索词 evidence')
        w.search_query.set('evidence');self.assertEqual(w.apply_search(),(1,1))
        self.assertEqual(w.selected,0)
        self.edit('修改后不再命中');w.finish_segment_edit()
        self.assertEqual(w.reader.search('evidence'),(0,0))

    def test_cancel_import_restores_saved_draft_and_document(self):
        w=self.window;identity=w.doc['id'];self.edit('导入前的校对')
        w.new_transcription();w.update()
        self.assertIsNone(w.doc)
        source=Path(self.temp.name)/'sample.wav';source.touch()
        with patch.object(app.filedialog,'askopenfilename',return_value=str(source)):w.choose_file()
        w.close_import_flow();w.update()
        self.assertEqual(w.doc['id'],identity)
        self.assertEqual(w.doc['segments'][0]['text'],'导入前的校对')
        w.geometry('960x680');w.update()
        self.assertTrue(w.audio_panel.winfo_viewable())
        self.assertGreater(w.audio_panel.winfo_height(),110)
        self.assertLessEqual(w.audio_panel.winfo_rooty()+w.audio_panel.winfo_height(),w.winfo_rooty()+w.winfo_height())

    def test_legacy_summary_survives_storage_but_not_normal_exports(self):
        w=self.window
        w.doc.update(summary='OLD ARCHIVE',summary_job={'complete':False},previous_summary={'text':'OLDER'})
        w.load_document(w.doc,persisted=False);w.flush_edits()
        for attribute in ('summary_reader','summary_editor','summary_button','auto_summary'):
            self.assertFalse(hasattr(w,attribute))
        saved=json.loads((Path(self.temp.name)/(w.doc['id']+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['summary'],'OLD ARCHIVE')
        for kind in ('md','json','txt','srt'):
            self.assertNotIn('OLD ARCHIVE',export_text(w.doc,kind))
        self.assertIn('OLD ARCHIVE',export_text(w.doc,'json',include_summary=True))
        self.assertNotIn('摘要',w.models_label.get())

    def test_start_and_resume_never_generate_summary(self):
        w=self.window;calls=[]
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
            def join(self,timeout=None):pass
        def pipeline(document,*args,**kwargs):calls.append(kwargs);document['status']='已转写';return document
        for resume in (False,True):
            with patch.object(app.threading,'Thread',ImmediateThread),patch.object(app,'run_local_pipeline',side_effect=pipeline):
                w.launch_job(resume=resume)
                w.drain_events()
        self.assertEqual(len(calls),2)
        for call in calls:
            self.assertFalse(call['auto_summary']);self.assertFalse(call['summary_only'])
        self.assertEqual([x['resume'] for x in calls],[False,True])
        self.assertTrue(w.check_ready())

    def test_inline_title_rename_cancel_and_save(self):
        w=self.window;original=w.doc['title']
        w.rename_document();w.rename_value.set('取消名称');w.cancel_document_name()
        self.assertEqual(w.doc['title'],original)
        w.rename_document();w.rename_value.set('商务访谈');w.save_document_name()
        self.assertEqual(w.doc['title'],'商务访谈')
        self.assertEqual(w.history.items[0]['title'],'商务访谈')

    def test_failed_save_keeps_library_selection_on_current_document(self):
        w=self.window;w.flush_edits();identity=w.doc['id']
        other=copy.deepcopy(w.doc);other['id']=new_document('other.wav')['id']
        save_document(other,self.temp.name);w.refresh_history()
        w.doc['segments'][0]['text']='尚未保存'
        w.history.selection_set(next(i for i,d in enumerate(w.history_docs) if d['id']==other['id']))
        with patch.object(app,'save_document',side_effect=OSError('disk full')),patch.object(app.messagebox,'showerror'):
            w.open_history()
        self.assertEqual(w.doc['id'],identity)
        self.assertEqual(w.history.items[w.history.curselection()[0]]['id'],identity)

    def test_drawer_size_focus_cycle_and_escape(self):
        w=self.window;w.new_transcription();w.update();drawer=w.import_drawer
        self.assertGreater(drawer.winfo_width(),400)
        self.assertGreater(drawer.winfo_height(),700)
        self.assertIs(w.grab_current(),drawer)
        drawer._tab(SimpleNamespace(state=0));w.update()
        self.assertIn(w.focus_get(),drawer._focusables())
        # The class handler returns break and restores the previous workspace.
        w.focus_get().event_generate('<Escape>');w.update()
        self.assertFalse(drawer.shown);self.assertIsNone(w.grab_current())

    def test_long_transcript_stays_one_reader_and_correct_font(self):
        w=self.window;doc=copy.deepcopy(w.doc)
        doc['segments']=[{'start_ms':i*2000,'end_ms':i*2000+1800,'speaker':'0','text':f'第 {i} 段 discussion evidence.'} for i in range(775)]
        w.load_document(doc);w.update()
        self.assertEqual(w.reader.segment_count,775)
        self.assertEqual(w.reader.text._reading_size,14)
        w.reader.select(774);self.assertEqual(w.selected,774)
        self.edit('最后一段修订',774);w.finish_segment_edit()
        self.assertEqual(w.doc['segments'][774]['text'],'最后一段修订')

    def test_reopening_demo_never_adds_library_copies(self):
        w=self.window
        self.assertEqual(w.doc['id'],app.DEMO_DOCUMENT_ID)
        self.edit('演示里的校对');w.finish_segment_edit()
        for _ in range(3):w.open_example();w.update()
        w.flush_edits();w.refresh_history()
        self.assertEqual(len(w.history_docs),1)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.json'))),1)
        self.assertEqual(w.doc['segments'][0]['text'],'演示里的校对')

    def test_typing_search_while_correcting_keeps_draft_and_paragraph(self):
        w=self.window;original=w.doc['segments'][0]['text'];self.edit('尚未确认 draft')
        w.search_query.set('字幕');self.assertEqual(w.apply_search(),(1,1))
        self.assertTrue(w._editor_open);self.assertEqual(w.selected,0)
        self.assertEqual(w.doc['segments'][0]['text'],original)
        self.assertEqual(w.segment_editor.get('1.0','end-1c'),'尚未确认 draft')
        # An empty draft must not pull focus away from the search field either.
        w.segment_editor.delete('1.0','end');w.search_entry.focus_set();w.update()
        w.search_query.set('测试');w.apply_search();w.update()
        self.assertTrue(w._editor_open);self.assertEqual(w.selected,0)
        w.cancel_segment_edit()

    def test_shortcut_and_speaker_rename_leave_paragraph_draft_uncommitted(self):
        w=self.window;original=w.doc['segments'][0]['text'];self.edit('改名前的草稿')
        w.focus_editor()
        self.assertTrue(w._editor_open);self.assertEqual(w.doc['segments'][0]['text'],original)
        w.rename_speaker();w.speaker_name_value.set('主持人');w.save_speaker_name();w.update()
        saved=json.loads((Path(self.temp.name)/(w.doc['id']+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['speakers']['0'],'主持人')
        self.assertEqual(saved['segments'][0]['text'],original)
        self.assertTrue(w._editor_open)
        self.assertEqual(w.segment_editor.get('1.0','end-1c'),'改名前的草稿')
        w.cancel_segment_edit()
        self.assertEqual(w.doc['segments'][0]['text'],original)

    def test_discarded_correction_can_be_restored(self):
        w=self.window;original=w.doc['segments'][1]['text']
        self.edit('误按了取消的长修改',1);w.cancel_segment_edit();w.update()
        self.assertEqual(w.doc['segments'][1]['text'],original)
        self.assertIn('Ctrl Z',w.save_status.get())
        w.reader.select(3);w.restore_discarded_edit();w.update()
        self.assertTrue(w._editor_open);self.assertEqual(w.selected,1)
        self.assertEqual(w.segment_editor.get('1.0','end-1c'),'误按了取消的长修改')
        w.finish_segment_edit()
        self.assertEqual(w.doc['segments'][1]['text'],'误按了取消的长修改')
        self.assertIsNone(w.restore_discarded_edit())
        self.assertFalse(w._editor_open)

    def test_compact_window_editor_keeps_heading_and_actions_visible(self):
        w=self.window;w.geometry('960x680');w.update()
        for index in (0,3):
            w.open_segment_editor(index);w.update();w.update()
            reader=w.reader.text
            top,bottom=reader.winfo_rooty(),reader.winfo_rooty()+reader.winfo_height()
            self.assertIsNotNone(reader.bbox(w.reader._start_mark(index)))
            save=w.editor_toggle_button
            self.assertTrue(save.winfo_viewable())
            self.assertGreaterEqual(save.winfo_rooty(),top)
            self.assertLessEqual(save.winfo_rooty()+save.winfo_height(),bottom)
            self.assertLess(w.editor_body.winfo_height(),reader.winfo_height())
            w.cancel_segment_edit();w.update()
        short=w.editor_body._desired_height
        self.edit('很长的一段校对文字，用来确认校对框会随内容增高。'*12);w.update()
        self.assertGreater(w.editor_body._desired_height,short)
        w.cancel_segment_edit()

    def test_long_title_is_shortened_on_screen_but_kept_in_document(self):
        w=self.window;long='季度经营复盘与下阶段产品规划讨论'*8
        w.rename_document();w.rename_value.set(long);w.save_document_name();w.update()
        self.assertEqual(w.doc['title'],long[:120])
        self.assertTrue(w.page_title.get().endswith('…'))
        self.assertLess(len(w.page_title.get()),len(w.doc['title']))
        w.rename_document()
        self.assertEqual(w.rename_value.get(),long[:120])
        w.cancel_document_name()

    def test_search_fields_show_hint_only_while_empty(self):
        w=self.window;w.reader.text.focus_set();w.update()
        self.assertEqual(w.search_hint.winfo_manager(),'place')
        self.assertEqual(w.history_search_hint.winfo_manager(),'place')
        w.search_query.set('字幕');w.update()
        self.assertEqual(w.search_hint.winfo_manager(),'')
        w.search_query.set('');w.update()
        self.assertEqual(w.search_hint.winfo_manager(),'place')

    def test_event_polling_survives_a_failing_handler(self):
        w=self.window;w.after_cancel(w.poll_timer);w.poll_timer=None
        w.events.put(('document',{'broken':True}))
        with self.assertRaises(KeyError):w.drain_events()
        self.assertIsNotNone(w.poll_timer)
        self.assertFalse(w.busy)

    def test_export_file_name_drops_characters_windows_rejects(self):
        w=self.window;w.doc['title']='Q3 复盘 / 产品: 路线?'
        with patch.object(app.filedialog,'asksaveasfilename',return_value='') as choose:
            w.export('txt')
        self.assertEqual(choose.call_args.kwargs['initialfile'],'Q3 复盘 _ 产品_ 路线_.txt')


if __name__=='__main__':unittest.main()
