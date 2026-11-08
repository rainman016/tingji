"""Durable summary nodes, owned by the existing pipeline job lock."""
import hashlib
import json
from pathlib import Path
from checkpoint_store import CheckpointStore, fingerprint
from core import AppError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


class SummaryCache:
    def __init__(self, directory, identity):
        self.store = CheckpointStore(Path(directory) / digest(identity))
        self.reused = 0

    def generate(self, key, material, merge, generate, report):
        parts = key.split('-')
        label = (f'第 {parts[1]} 段摘要' if len(parts) == 2 and parts[0] == 'map'
                 else f'第 {parts[1]} 轮合并' if len(parts) == 3 and parts[0] == 'merge'
                 else '摘要步骤')
        identity = digest({'material': material, 'merge': merge})
        saved = self.store.load_unit(key)
        if saved is not None:
            if saved.get('input') != identity or not isinstance(saved.get('text'), str) or not saved['text'].strip():
                raise AppError('摘要恢复数据与本次材料不一致，旧进度已保留；请重新生成摘要。')
            self.reused += 1
            report('已复用 · ' + label)
            return saved['text']
        text = generate(material, merge)
        self.store.save_unit(key, {'input': identity, 'text': text})
        report('已保存 · ' + label)
        return text


def open_cache(request, model_path, prompts):
    directory = request.get('summary_checkpoint_dir')
    job = request.get('document', {}).get('summary_job', {})
    if not directory or not job.get('run_id'):
        return None
    return SummaryCache(directory, {'schema': 3, 'run': job['run_id'], 'source': job['source'],
        'model': fingerprint(model_path), 'prompts': prompts, 'context': 6144,
        'max_tokens': 1500, 'temperature': .2, 'top_p': .8})
