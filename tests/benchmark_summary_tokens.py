"""Explicit fixed-output CPU/GPU comparison; run each backend in a fresh process."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_worker import block_network
from summary_backend import load_summary_model

PROMPT = ('<|im_start|>user\n请用中文连续写一篇不少于1000字的技术文章，'
          '逐条深入解释缓存、并发、事务、幂等、重试、校验、监控、回滚和降级。'
          '每个主题至少写一段，不要提前结束。/no_think<|im_end|>\n'
          '<|im_start|>assistant\n<think>\n\n</think>\n\n')


def run(device, output):
    block_network()
    model, info = load_summary_model({'device': 'cpu'} if device == 'cpu' else {'device': 'auto'}, lambda _: None, context=2048)
    try:
        assert info['device'] == device
        started = time.monotonic()
        response = model.create_completion(prompt=PROMPT, max_tokens=128, temperature=0,
                                           stop=['<|im_end|>'], echo=False)
        elapsed = time.monotonic() - started
        tokens = response['usage']['completion_tokens']
        assert tokens == 128, response['usage']
        info.update(output_tokens=tokens, generation_seconds=round(elapsed, 3),
                    effective_tokens_per_second=round(tokens / elapsed, 2),
                    prompt_tokens=response['usage']['prompt_tokens'])
    finally:
        model.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(info, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('device', choices=('cpu', 'gpu'))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.device, args.output.resolve())
