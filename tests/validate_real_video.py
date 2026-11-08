"""Explicit real-media acceptance runner. Requires user authorization; not a unit test."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Cancelled, export_text, new_document, probe_media_duration, timestamp
from local_service import LocalService, run_local_pipeline
from process_control import start_owned_process
from transcription_recovery import checkpoint_for, recovery_info

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def process_tree_rss():
    """Sample this runner and descendants through Windows APIs, without psutil."""
    if os.name != 'nt':
        return 0
    class Entry(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('usage', wt.DWORD), ('pid', wt.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wt.DWORD), ('threads', wt.DWORD),
                    ('parent', wt.DWORD), ('priority', wt.LONG), ('flags', wt.DWORD), ('exe', wt.WCHAR * 260)]
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wt.DWORD), ('faults', wt.DWORD)] + [(name, ctypes.c_size_t) for name in
                    ('peak_ws', 'ws', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return 0
    parents = {}
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            parents[entry.pid] = entry.parent
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    owned = {os.getpid()}
    while True:
        children = {pid for pid, parent in parents.items() if parent in owned}
        if children <= owned:
            break
        owned |= children
    total = 0
    for pid in owned:
        handle = kernel.OpenProcess(0x410, False, pid)
        if handle:
            try:
                counters = Counters(); counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    total += counters.ws
            finally:
                kernel.CloseHandle(handle)
    return total


def available_memory_mib():
    if os.name != 'nt':
        return None
    class Status(ctypes.Structure):
        _fields_ = [('length', wt.DWORD), ('load', wt.DWORD)] + [(name, ctypes.c_ulonglong) for name in
                    ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
    status = Status(); status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return round(status.available / 1048576, 1)


class Monitor:
    def __init__(self, directory, cancel):
        self.directory, self.cancel = directory, cancel
        self.done = threading.Event()
        self.peak_rss = self.peak_gpu = self.baseline_gpu = 0
        self.samples = 0
        self.minimum_available = None
        self.errors = []
        self.interrupt_store = None
        self.interrupt_after = 0
        self.intentional_stop = False
        self.thread = threading.Thread(target=self.run, name='acceptance-resource-monitor')

    def __enter__(self):
        self.thread.start()
        return self

    def run(self):
        while not self.done.is_set():
            try:
                self.peak_rss = max(self.peak_rss, process_tree_rss())
                free = available_memory_mib()
                if free is not None:
                    self.minimum_available = free if self.minimum_available is None else min(free, self.minimum_available)
                if self.samples % 2 == 0:
                    query = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                                           capture_output=True, text=True, timeout=3,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    used = int(query.stdout.strip().splitlines()[0])
                    if self.samples == 0:
                        self.baseline_gpu = used
                    self.peak_gpu = max(self.peak_gpu, used)
                self.samples += 1
                if (self.directory / 'STOP').exists():
                    self.cancel.set()
                if self.interrupt_store is not None and self.interrupt_after:
                    units = list((self.interrupt_store.directory / 'units').glob('asr-*.json'))
                    if len(units) >= self.interrupt_after:
                        self.intentional_stop = True
                        self.cancel.set()
                write_json(self.directory / 'resources.json', self.result())
            except Exception as exc:
                if len(self.errors) < 3:
                    self.errors.append(str(exc)[:180])
            self.done.wait(5)

    def result(self):
        return {'peak_process_tree_rss_mib': round(self.peak_rss / 1048576, 1),
                'baseline_gpu_card_used_mib': self.baseline_gpu, 'peak_gpu_card_used_mib': self.peak_gpu,
                'gpu_scope': 'whole GPU including other applications', 'samples': self.samples,
                'minimum_system_available_mib': self.minimum_available,
                'sampling_errors': list(self.errors)}

    def __exit__(self, *_):
        self.done.set(); self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Resource monitor did not exit')


def ffmpeg(command):
    with start_owned_process(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
        _, errors = process.process.communicate(timeout=60)
        if process.returncode:
            raise RuntimeError(errors.decode('utf-8', 'replace')[-600:])


def overlaps(start, end, intervals):
    return sum(max(0, min(end, right) - max(start, left)) for left, right in intervals)


def uncovered_intervals(speech, transcript):
    gaps = []
    for start, end in speech:
        cursor = start
        for left, right in transcript:
            if right <= cursor:
                continue
            if left >= end:
                break
            if left > cursor:
                gaps.append((cursor, min(left, end)))
            cursor = max(cursor, min(right, end))
        if cursor < end:
            gaps.append((cursor, end))
    return gaps


def assess(document, audio):
    segments = document['segments']
    duration = document['duration_ms']
    errors, warnings = [], []
    if not segments:
        errors.append('No transcript')
    if document.get('language') != 'en':
        errors.append('English media was not routed to English')
    for i, row in enumerate(segments):
        if not 0 <= row['start_ms'] < row['end_ms'] <= duration:
            errors.append(f'Invalid timestamp at {i}')
        if i and row['start_ms'] < segments[i - 1]['start_ms']:
            errors.append(f'Out of order at {i}')
        if row['speaker'] not in document['speakers']:
            errors.append(f'Missing speaker at {i}')
        if i and re.sub(r'\W+', '', row['text'].lower()) == re.sub(r'\W+', '', segments[i - 1]['text'].lower()):
            warnings.append({'type': 'adjacent_identical_text', 'start_ms': row['start_ms'], 'text': row['text'][:160]})
    speaker_counts = {key: sum(row['speaker'] == key for row in segments) for key in document['speakers']}
    result = {'duration_ms': duration, 'segments': len(segments), 'speakers': speaker_counts,
              'last_text_end_ms': segments[-1]['end_ms'] if segments else None,
              'errors': errors, 'warnings': warnings,
              'sample_text': [{key: row[key] for key in ('start_ms', 'end_ms', 'speaker', 'text')}
                              for row in segments[:3] + segments[-2:]]}
    if audio is not None:
        import numpy as np
        from faster_whisper.vad import get_speech_timestamps
        with wave.open(str(audio), 'rb') as stream:
            pcm = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype('float32') / 32768
        speech = [(round(part['start'] / 16), round(part['end'] / 16)) for part in
                  get_speech_timestamps(pcm, sampling_rate=16000, speech_pad_ms=0)]
        speech_ms = sum(end - start for start, end in speech)
        transcript_ranges = []
        for row in segments:
            start, end = row['start_ms'], row['end_ms']
            if transcript_ranges and start <= transcript_ranges[-1][1]:
                transcript_ranges[-1] = (transcript_ranges[-1][0], max(end, transcript_ranges[-1][1]))
            else:
                transcript_ranges.append((start, end))
        covered = sum(overlaps(start, end, transcript_ranges) for start, end in speech)
        result['vad_speech_ms'] = speech_ms
        result['speech_time_coverage'] = round(covered / speech_ms, 4) if speech_ms else None
        uncovered = [{'start_ms': start, 'end_ms': end} for start, end in speech
                     if end - start >= 1000 and overlaps(start, end, transcript_ranges) < (end - start) * .5]
        result['undercovered_speech_regions'] = uncovered
        result['uncovered_speech_spans_over_1s'] = [
            {'start_ms': start, 'end_ms': end}
            for start, end in uncovered_intervals(speech, transcript_ranges) if end - start >= 1000]
    return result


def audit_reports(output):
    report_file = output / 'samples-report.json'
    report = json.loads(report_file.read_text(encoding='utf-8'))
    for case in report['cases']:
        doc = json.loads((output / 'samples' / (case['document_id'] + '.json')).read_text(encoding='utf-8'))
        case.setdefault('initial_padded_speech_time_coverage', case['speech_time_coverage'])
        case.update(assess(doc, output / (case['name'] + '.wav')))
        print(case['name'], 'unpadded_speech_coverage=', case['speech_time_coverage'],
              'uncovered_spans=', case['uncovered_speech_spans_over_1s'], flush=True)
    write_json(report_file, report)
    state = json.loads((output / 'full-state.json').read_text(encoding='utf-8'))
    doc = json.loads((ROOT / 'results' / (state['id'] + '.json')).read_text(encoding='utf-8'))
    citations = re.findall(r'\[(\d{2}:\d{2}:\d{2})\]', doc.get('summary', ''))
    allowed = {timestamp(row['start_ms']) for row in doc['segments']}
    result = {'summary_citations': citations, 'unknown_summary_citations': sorted(set(citations) - allowed),
              'summary_characters': len(doc.get('summary', '')), 'document_id': doc['id']}
    if not citations:
        result['note'] = 'No navigable timestamp citations generated'
    write_json(output / 'final-audit.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def run(source, output, phase, interrupt_after_chunks=0):
    import imageio_ffmpeg
    output.mkdir(parents=True, exist_ok=True)
    cancel = threading.Event()
    started = time.monotonic()
    log = (output / f'{phase}-progress.log').open('a', encoding='utf-8')
    def progress(message):
        log.write(time.strftime('%H:%M:%S') + ' ' + message + '\n'); log.flush()
    report = {'source': str(source), 'phase': phase, 'cases': [], 'status': 'running'}
    try:
        with Monitor(output, cancel) as monitor:
            duration = probe_media_duration(source, cancel)
            if phase == 'samples':
                for name, offset in [('opening', 0), ('middle', max(0, duration // 2 - 75000)), ('ending', max(0, duration - 150000))]:
                    clip = output / (name + '.wav')
                    executable = imageio_ffmpeg.get_ffmpeg_exe()
                    ffmpeg([executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(offset / 1000),
                            '-i', str(source), '-t', '150', '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                            '-c:a', 'pcm_s16le', str(clip)])
                    ffmpeg([executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-ss', str((offset + 45000) / 1000),
                            '-i', str(source), '-frames:v', '1', '-vf', 'scale=960:-1', str(output / (name + '.png'))])
                    doc = new_document(clip); doc['title'] = name + ' · real-media acceptance'
                    case_start = time.monotonic()
                    progress(f'SAMPLE {name}: source offset {timestamp(offset)}')
                    run_local_pipeline(doc, output / 'samples', cancel, progress, language='auto', auto_summary=False)
                    case = assess(doc, clip)
                    case.update(name=name, source_offset_ms=offset, seconds=round(time.monotonic() - case_start, 2),
                                document_id=doc['id'], detection=doc.get('language_detection'))
                    report['cases'].append(case)
                    write_json(output / 'samples-report.json', report)
                    (output / (name + '.txt')).write_text(export_text(doc, 'txt'), encoding='utf-8')
                    print(name, 'seconds=', case['seconds'], 'segments=', case['segments'], 'speakers=', case['speakers'],
                          'speech_coverage=', case.get('speech_time_coverage'), 'errors=', case['errors'], flush=True)
                    if case['errors']:
                        raise RuntimeError(f'{name} failed structural acceptance')
            else:
                state_file = output / 'full-state.json'
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding='utf-8'))
                    doc = json.loads((ROOT / 'results' / (state['id'] + '.json')).read_text(encoding='utf-8'))
                    resume = not doc.get('transcription_complete')
                    summary_only = bool(doc.get('transcription_complete'))
                else:
                    doc = new_document(source); doc['title'] += ' · 第四阶段验收'
                    write_json(state_file, {'id': doc['id'], 'source': str(source)})
                    resume = summary_only = False
                store = checkpoint_for(ROOT / 'results', doc['id'])
                proof_file = output / 'resume-proof.json'
                if interrupt_after_chunks:
                    monitor.interrupt_store = store
                    monitor.interrupt_after = interrupt_after_chunks
                proof = json.loads(proof_file.read_text(encoding='utf-8')) if proof_file.exists() else {}
                run_local_pipeline(doc, ROOT / 'results', cancel, progress, language='auto', resume=resume,
                                   summary_only=summary_only, auto_summary=True)
                case = assess(doc, None)
                case.update(document_id=doc['id'], summary_characters=len(doc.get('summary', '')),
                            cache_audio_released=not checkpoint_for(ROOT / 'results', doc['id']).audio_path.exists())
                for key, stamp in proof.items():
                    if (store.directory / 'units' / key).stat().st_mtime_ns != stamp:
                        raise RuntimeError('A committed ASR unit was rewritten during resume: ' + key)
                case['verified_reused_chunks'] = len(proof)
                report['cases'].append(case)
                for kind in ('txt', 'srt', 'md', 'json'):
                    (output / ('full.' + kind)).write_text(export_text(doc, kind), encoding='utf-8-sig' if kind in ('txt', 'srt') else 'utf-8')
                if case['errors'] or not doc.get('summary'):
                    raise RuntimeError('Full video failed final result/summary validation')
            report['resources'] = monitor.result()
        report['seconds'] = round(time.monotonic() - started, 2)
        report['status'] = 'passed'
    except BaseException as exc:
        intentional = isinstance(exc, Cancelled) and monitor.intentional_stop
        report.update(status='interrupted' if intentional else 'stopped' if isinstance(exc, Cancelled) else 'failed', error=str(exc),
                      seconds=round(time.monotonic() - started, 2))
        report['resources'] = monitor.result()
        if intentional:
            proof = {path.name: path.stat().st_mtime_ns for path in (store.directory / 'units').glob('asr-*.json')}
            write_json(output / 'resume-proof.json', proof)
            report['recovery'] = recovery_info(doc, ROOT / 'results')
            write_json(output / 'full-interrupted-report.json', report)
        else:
            raise
    finally:
        log.close()
        write_json(output / (phase + '-report.json'), report)
    print(json.dumps({key: report[key] for key in ('phase', 'status', 'seconds', 'resources')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('samples', 'full', 'audit'))
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--interrupt-after-chunks', type=int, default=0)
    args = parser.parse_args()
    if args.phase == 'audit':
        audit_reports(args.output.resolve())
    else:
        run(args.source.resolve(), args.output.resolve(), args.phase, args.interrupt_after_chunks)
