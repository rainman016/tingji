"""Opt-in, sequential real-GPU stop/restart acceptance; never runs in unit discovery."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Cancelled, new_document, export_text
from local_service import run_local_pipeline
from summary_state import FIELDS


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def run(mode, source, folder):
    folder.mkdir(parents=True, exist_ok=True)
    original = source.read_bytes()
    results = folder / 'results'
    if mode == 'stop':
        doc = json.loads(original)
        doc['id'] = new_document(doc['source'])['id']
        doc['title'] = '摘要恢复验收'
        doc['summary'] = ''
        for key in (*FIELDS, 'summary_job', 'previous_summary'):
            doc.pop(key, None)
    else:
        state = json.loads((folder / 'stopped.json').read_text(encoding='utf-8'))
        doc = json.loads((results / (state['id'] + '.json')).read_text(encoding='utf-8'))
    cancel = threading.Event()
    started = time.monotonic()
    with (folder / (mode + '.log')).open('w', encoding='utf-8') as log:
        def report(message):
            log.write(message + '\n'); log.flush()
            if mode == 'stop' and len(list(results.rglob('map-*.json'))) >= 2:
                cancel.set()
        try:
            run_local_pipeline(doc, results, cancel, report, summary_only=True, device='auto')
        except Cancelled:
            if mode != 'stop': raise
    assert source.read_bytes() == original
    elapsed = round(time.monotonic() - started, 3)
    if mode == 'stop':
        files = {str(p.relative_to(folder)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                 'mtime_ns': p.stat().st_mtime_ns} for p in results.rglob('map-*.json')}
        assert len(files) >= 2 and doc['status'] == '已停止'
        save(folder / 'stopped.json', {'id': doc['id'], 'seconds': elapsed, 'files': files})
        print(json.dumps({'status': 'stopped', 'saved_nodes': len(files), 'seconds': elapsed}))
    else:
        for name, before in state['files'].items():
            file = folder / name
            assert file.stat().st_mtime_ns == before['mtime_ns']
            assert hashlib.sha256(file.read_bytes()).hexdigest() == before['sha256']
        info = doc['summary_inference']
        assert info['device'] == 'gpu' and info['reused_nodes'] >= 2
        assert doc['status'] == '已完成' and doc['summary_job']['complete']
        reference = json.loads(original)
        assert doc['segments'] == reference['segments'] and doc['speakers'] == reference['speakers']
        section = doc['summary'].split('核心要点', 1)[-1].split('原文回看', 1)[0]
        times = [sum(int(value) * factor for value, factor in zip(stamp.split(':'), (3600, 60, 1)))
                 for stamp in re.findall(r'\[(\d{2}:\d{2}:\d{2})\]', section)]
        duration = max(row['end_ms'] for row in doc['segments']) / 1000
        coverage = sorted({min(2, int(value / duration * 3)) for value in times})
        assert coverage == [0, 1, 2], 'Representative long-document points must cover all thirds'
        save(folder / 'report.json', {'status': 'passed', 'seconds': elapsed,
             'total_compute_seconds': round(elapsed + state['seconds'], 3), 'inference': info,
             'validation': doc['summary_validation'], 'committed_nodes_unchanged': True,
             'source_document_unchanged': True, 'temporal_coverage_thirds': coverage})
        (folder / 'summary.md').write_text(doc['summary'], encoding='utf-8')
        print(json.dumps({'status': 'passed', 'seconds': elapsed, 'reused_nodes': info['reused_nodes']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['stop', 'resume'])
    parser.add_argument('source', type=Path)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    run(args.mode, args.source.resolve(), args.folder.resolve())
