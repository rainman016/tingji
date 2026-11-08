import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from core import AppError
from summary_resume import SummaryCache, open_cache
from summary_state import snapshot, restore, mark_edited, source_digest


class SummaryRevisionTests(unittest.TestCase):
    def test_restore_moves_metadata_with_text(self):
        doc = {'summary': '旧版本', 'summary_inference': {'device': 'cpu'},
               'summary_validation': {'issues': ['old']}, 'summary_original': 'old raw'}
        old = snapshot(doc)
        doc.update(summary='新版本', summary_inference={'device': 'gpu'}, summary_original='new raw')
        restore(doc, old)
        self.assertEqual(doc['summary_inference']['device'], 'cpu')
        self.assertEqual(doc['summary_original'], 'old raw')
        self.assertEqual(doc['previous_summary']['metadata']['summary_inference']['device'], 'gpu')
        self.assertTrue(doc['summary_stale'])

    def test_legacy_revision_cannot_inherit_new_metadata(self):
        doc = {'summary': 'new', 'summary_inference': {'device': 'gpu'}, 'summary_validation': {}}
        restore(doc, {'text': 'legacy'})
        self.assertNotIn('summary_inference', doc)
        self.assertNotIn('summary_validation', doc)

    def test_manual_edit_preserves_text_but_rechecks_invalid_citation(self):
        doc = {'summary': '手动改写 [09:00:00]', 'segments': [], 'speakers': {}}
        mark_edited(doc)
        self.assertEqual(doc['summary'], '手动改写 [09:00:00]')
        self.assertEqual(doc['summary_review']['status'], 'edited')
        self.assertEqual(doc['summary_validation']['issues'][0]['kind'], 'unverified_time')

    def test_source_hash_ignores_ui_state_and_tracks_text_and_speakers(self):
        doc = {'segments': [{'text': 'one'}], 'speakers': {'0': 'Alice'}}
        original = source_digest(doc)
        doc['status'] = '生成摘要'
        self.assertEqual(source_digest(doc), original)
        doc['speakers']['0'] = 'Bob'
        self.assertNotEqual(source_digest(doc), original)


class SummaryResumeTests(unittest.TestCase):
    def test_real_cache_identity_invalidates_model_prompt_source_and_run_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'model.gguf'; model.write_bytes(b'original model fixture')
            request = {'summary_checkpoint_dir': str(Path(folder) / 'cache'),
                       'document': {'summary_job': {'run_id': 'one', 'source': 'original'}}}
            cache = open_cache(request, model, ['prompt'])
            cache.generate('map-1', 'text', False, lambda *_: 'old', lambda _: None)
            for change in ('prompt', 'source', 'run_id', 'model'):
                with self.subTest(change=change):
                    current = copy.deepcopy(request)
                    prompts = ['changed prompt'] if change == 'prompt' else ['prompt']
                    if change in ('source', 'run_id'):
                        current['document']['summary_job'][change] = 'changed'
                    if change == 'model': model.write_bytes(b'changed model fixture')
                    fresh = open_cache(current, model, prompts)
                    generate = Mock(return_value='new')
                    self.assertEqual(fresh.generate('map-1', 'text', False, generate, lambda _: None), 'new')
                    generate.assert_called_once()
                    self.assertEqual(fresh.reused, 0)

    def test_new_instance_reuses_committed_node_and_keeps_file_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = SummaryCache(folder, {'model': 'a', 'run': '1'})
            self.assertEqual(cache.generate('map-1', 'source', False, lambda *_: 'saved', lambda _: None), 'saved')
            file = next(Path(folder).rglob('map-1.json')); before = file.read_bytes(), file.stat().st_mtime_ns
            restarted = SummaryCache(folder, {'model': 'a', 'run': '1'})
            generate = Mock(side_effect=AssertionError('must not recompute'))
            self.assertEqual(restarted.generate('map-1', 'source', False, generate, lambda _: None), 'saved')
            self.assertEqual((file.read_bytes(), file.stat().st_mtime_ns), before)
            self.assertEqual(restarted.reused, 1)

    def test_failed_node_is_recomputed_without_losing_completed_node(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = SummaryCache(folder, {})
            cache.generate('map-1', 'one', False, lambda *_: 'first', lambda _: None)
            with self.assertRaises(RuntimeError):
                cache.generate('map-2', 'two', False, Mock(side_effect=RuntimeError('device lost')), lambda _: None)
            cache = SummaryCache(folder, {})
            self.assertEqual(cache.generate('map-2', 'two', False, lambda *_: 'second', lambda _: None), 'second')
            self.assertEqual(cache.generate('map-1', 'one', False, Mock(), lambda _: None), 'first')

    def test_changed_model_prompt_or_run_never_reuses_old_result(self):
        with tempfile.TemporaryDirectory() as folder:
            SummaryCache(folder, {'model': 'a'}).generate('map-1', 'one', False, lambda *_: 'old', lambda _: None)
            self.assertEqual(SummaryCache(folder, {'model': 'b'}).generate('map-1', 'one', False, lambda *_: 'new', lambda _: None), 'new')

    def test_changed_input_and_corrupt_unit_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = SummaryCache(folder, {})
            cache.generate('map-1', 'source', False, lambda *_: 'saved', lambda _: None)
            with self.assertRaises(AppError): cache.generate('map-1', 'changed', False, Mock(), lambda _: None)
            next(Path(folder).rglob('map-1.json')).write_text('{}')
            with self.assertRaises(AppError): cache.generate('map-1', 'source', False, Mock(), lambda _: None)


if __name__ == '__main__': unittest.main()
