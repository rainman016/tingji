"""Explicit GPU-only summary benchmark against an existing transcript, no ASR."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import export_text, new_document, timestamp, transcript_text
from local_service import LocalService, run_local_pipeline
from validate_real_video import Monitor, write_json


class StrictGPUService(LocalService):
    def summarize(self, document, style):
        result = self.run_worker({'mode': 'summary', 'document': document, 'style': style,
                                  'device': 'auto', 'summary_backend': 'gpu'}, timeout=900)
        self.last_summary_inference = result.get('inference', {})
        if self.last_summary_inference.get('device') != 'gpu' or self.last_summary_inference.get('offloaded_layers', 0) <= 0:
            raise RuntimeError('Benchmark requires actual GPU offload; refusing CPU fallback')
        return result['summary']


def run(document_path, output, baseline_seconds, require_coverage=False):
    output.mkdir(parents=True, exist_ok=True)
    source_bytes = document_path.read_bytes()
    source = json.loads(source_bytes.decode('utf-8-sig'))
    document = copy.deepcopy(source)
    document['id'] = new_document(source['source'])['id']
    document['title'] += ' · GPU摘要测速'
    document['summary'] = ''
    for key in ('summary_original', 'summary_validation', 'summary_review', 'previous_summary'):
        document.pop(key, None)
    material_hash = hashlib.sha256(transcript_text(document).encode('utf-8')).hexdigest()
    cancel = threading.Event()
    started = time.monotonic()
    report = {'status': 'running', 'cpu_baseline_seconds': baseline_seconds,
              'source_document': str(document_path), 'transcript_sha256': material_hash}
    with (output / 'progress.log').open('w', encoding='utf-8') as log:
        def progress(message):
            log.write(time.strftime('%H:%M:%S') + ' ' + message + '\n'); log.flush()
        try:
            with Monitor(output, cancel) as monitor:
                run_local_pipeline(document, output / 'results', cancel, progress, summary_only=True,
                                   device='auto', service_factory=StrictGPUService)
                elapsed = time.monotonic() - started
                info = document['summary_inference']
                assert document['segments'] == source['segments'] and document['speakers'] == source['speakers']
                assert document_path.read_bytes() == source_bytes
                assert len(document['summary']) >= 100
                section = document['summary'].split('核心要点', 1)[-1].split('原文回看', 1)[0]
                section = re.sub(r'说话人\s*\d+', '', section)
                assert len(re.findall(r'[\u3400-\u9fff]', section)) >= 30, 'Expected Chinese key points, not English quotes'
                assert 1 <= document['summary_validation'].get('key_points', 0) <= 7, 'Expected 1-7 key points'
                times = [sum(int(value) * factor for value, factor in zip(stamp.split(':'), (3600, 60, 1)))
                         for stamp in re.findall(r'\[(\d{2}:\d{2}:\d{2})\]', section)]
                duration = max(row['end_ms'] for row in document['segments']) / 1000
                coverage = sorted({min(2, int(value / duration * 3)) for value in times})
                if require_coverage:
                    assert coverage == [0, 1, 2], 'Key points must cover the beginning, middle and final thirds'
                citations = re.findall(r'\[(\d{2}:\d{2}:\d{2})\]', document['summary'])
                allowed = {timestamp(row['start_ms']) for row in document['segments']}
                assert not (set(citations) - allowed)
                for kind in ('md', 'json'):
                    (output / ('summary.' + kind)).write_text(export_text(document, kind), encoding='utf-8')
                report.update(status='passed', seconds=round(elapsed, 3), inference=info,
                              speedup=round(baseline_seconds / elapsed, 2) if baseline_seconds else None,
                              resources=monitor.result(), summary_characters=len(document['summary']),
                              citation_count=len(citations), validation=document.get('summary_validation'),
                              source_document_unchanged=True, transcript_unchanged=True,
                              chinese_key_points_checked=True, temporal_coverage_thirds=coverage)
        except BaseException as exc:
            report.update(status='failed', error=str(exc), seconds=round(time.monotonic() - started, 3))
            raise
        finally:
            write_json(output / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('document', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cpu-seconds', type=float)
    parser.add_argument('--require-coverage', action='store_true')
    args = parser.parse_args()
    run(args.document.resolve(), args.output.resolve(), args.cpu_seconds, args.require_coverage)
