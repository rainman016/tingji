"""Bounded structured model output, rendered as an editable document."""
import json
import re


def schema(style='通用摘要', merge=True, times=None):
    # Use the converter's JSON-safe character rule. Regex dots can admit bare
    # quotes and let generated content escape its intended field boundaries.
    chinese = {'type': 'string'}
    time = ({'type': 'string', 'enum': times} if times else
            {'type': 'string', 'pattern': '^[0-9]{2}:[0-5][0-9]:[0-5][0-9]$'})
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    return obj({
        'overview': chinese,
        'points': {'type': 'array', 'minItems': 1, 'maxItems': 7 if merge else 4,
                   'items': obj({'text': chinese, 'time': time})},
        'quotes': {'type': 'array', 'maxItems': 3 if merge else 1,
                   'items': obj({'text': {'type': 'string'},
                                 'time': time, 'speaker': {'type': 'string'}})},
        'actions': {'type': 'array', 'maxItems': 7 if style == '会议纪要' else 0,
                    'items': obj({'text': chinese, 'time': time,
                                  'owner': {'type': 'string'},
                                  'deadline': {'type': 'string'}})}})


def generate_checked(complete, grammar_factory, *, style, merge, times, report):
    """Use fast decoding normally; bound format repair to one additional call.

    Inference failures propagate unchanged so device fallback and cancellation
    remain the worker's responsibility. Only invalid output triggers repair.
    """
    for attempt in range(2):
        response = complete(grammar_factory() if attempt else None)
        try:
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ValueError('本地摘要未完整生成；文字稿已保存，可重试摘要。')
            data = parse(choice.get('text', '').strip())
            if len(data['points']) > (7 if merge else 4) or len(data['quotes']) > (3 if merge else 1):
                raise ValueError('摘要条目过多。')
            if style != '会议纪要' and data['actions']:
                raise ValueError('摘要模板不应包含待办。')
            if any(row['time'] not in times for key in ('points', 'quotes', 'actions') for row in data[key]):
                raise ValueError('摘要引用了材料之外的时间。')
            return json.dumps(data, ensure_ascii=False)
        except (ValueError, KeyError, IndexError, TypeError):
            if attempt:
                raise
            report('正在校正摘要格式，已完成的分段仍保留…')


def parse(text):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {'overview', 'points', 'quotes', 'actions'}:
        raise ValueError('摘要结构不完整，请重试摘要。')
    if not isinstance(data['overview'], str) or len(re.findall('[一-鿿]', data['overview'])) < 4:
        raise ValueError('摘要概览未使用中文，请重试摘要。')
    for field, limit in (('points', 7), ('quotes', 3), ('actions', 7)):
        rows = data[field]
        if not isinstance(rows, list) or len(rows) > limit or (field == 'points' and not rows):
            raise ValueError('摘要条目数量无效，请重试摘要。')
        for row in rows:
            fields = {'text', 'time'} | ({'speaker'} if field == 'quotes' else {'owner', 'deadline'} if field == 'actions' else set())
            if not isinstance(row, dict) or set(row) != fields or any(not isinstance(v, str) for v in row.values()):
                raise ValueError('摘要条目结构无效，请重试摘要。')
            if not re.fullmatch(r'\d{2}:[0-5]\d:[0-5]\d', row['time']):
                raise ValueError('摘要时间格式无效：' + repr(row['time'][:24]) + '，请重试摘要。')
            if field != 'quotes' and len(re.findall('[一-鿿]', row['text'])) < 4:
                raise ValueError('摘要要点未使用中文，请重试摘要。')
    return data


def render(text):
    data = parse(text)
    def line(value): return ' '.join(value.split())
    output = ['## 概览', line(data['overview']), '', '## 核心要点']
    output.extend(f"- {line(row['text'])} [{row['time']}]" for row in data['points'])
    if data['quotes']:
        output.extend(['', '## 原文回看'])
        output.extend(f"[{row['time']}] {line(row['speaker'])}：{line(row['text'])}" for row in data['quotes'])
    if data['actions']:
        output.extend(['', '## 待办事项'])
        output.extend(f"- {line(row['text'])}（负责人：{line(row['owner']) or '未提及'}；期限：{line(row['deadline']) or '未提及'}）[{row['time']}]"
                      for row in data['actions'])
    return '\n'.join(output)


def balance_points(merged, partials, limit=7):
    """Reserve room across the source instead of trusting merge order alone.

    Prefer the merged wording where its source anchor belongs to that block.
    With more blocks than slots, sample evenly including both end blocks.
    No new assertion or source timestamp is synthesized here.
    """
    if len(partials) <= 1:
        return merged
    final = parse(merged)
    blocks = [parse(text)['points'] for text in partials]
    count = min(limit, len(blocks))
    indices = [round(i * (len(blocks) - 1) / (count - 1)) for i in range(count)]
    selected, seen = [], set()
    for index in indices:
        candidates = blocks[index]
        times = {row['time'] for row in candidates}
        preferred = [row for row in final['points'] if row['time'] in times]
        for row in preferred + candidates:
            if row['time'] not in seen:
                selected.append(row)
                seen.add(row['time'])
                break
    for row in final['points']:
        if len(selected) >= limit:
            break
        if row['time'] not in seen:
            selected.append(row)
            seen.add(row['time'])
    final['points'] = sorted(selected, key=lambda row: row['time'])
    return json.dumps(final, ensure_ascii=False)
