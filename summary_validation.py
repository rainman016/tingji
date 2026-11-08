"""Ground literal summary quotes in source text; never invent replacement times."""
import re

from core import timestamp

STAMP = r'\d{2}:\d{2}:\d{2}'
QUOTE = re.compile(r'^\s*(?:(?:[-*]|\d+[.)、])\s*)?\[(' + STAMP + r')(?:\s*[-–—]\s*(' + STAMP + r'))?\]\s*([^：:\n]{1,60})[：:]\s*(.+?)\s*$')
CITATION = re.compile(r'\[(' + STAMP + r')\]')


def literal(text):
    # Keep English word boundaries; Chinese characters remain individually
    # matchable without making "now here" equivalent to "nowhere".
    return ' '.join(re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', text.casefold()))


def validate_summary(text, document):
    segments = document.get('segments', [])
    allowed = {timestamp(row['start_ms']) for row in segments}
    indexed = []
    for i, row in enumerate(segments):
        first = literal(row['text'])
        combined, previous = first, row
        indexed.append((row, combined, len(first)))
        for following in segments[i + 1:i + 3]:
            if following['speaker'] != row['speaker'] or following['start_ms'] - previous['end_ms'] > 2000:
                break
            combined += ' ' + literal(following['text'])
            indexed.append((row, combined, len(first)))
            previous = following
    issues, output = [], []
    corrected = 0
    for line in text.splitlines():
        quote = QUOTE.match(line)
        if quote:
            start, end, speaker, body = quote.groups()
            normalized = literal(body)
            candidates = {}
            for row, source, first_length in indexed:
                match = (re.search(r'(?<!\S)' + re.escape(normalized) + r'(?!\S)', source)
                         if len(normalized.replace(' ', '')) >= 12 else None)
                if match is not None and match.start() < first_length:
                    candidates[(row['start_ms'], row['end_ms'], row['speaker'])] = row
            matches = list(candidates.values())
            preferred = [row for row in matches if timestamp(row['start_ms']) == start]
            matches = preferred or matches
            if len(matches) == 1:
                row = matches[0]
                actual_time = timestamp(row['start_ms'])
                actual_speaker = document['speakers'].get(row['speaker'], '未区分说话人')
                if actual_time != start or end or actual_speaker != speaker:
                    corrected += 1
                    issues.append({'kind': 'corrected_quote_anchor', 'old_time': start,
                                   'time': actual_time, 'speaker': actual_speaker})
                output.append(f'[{actual_time}] {actual_speaker}：{body}')
                continue
            original_time = start + ('—' + end if end else '')
            output.append(f'转述（时间待核对：{original_time}）：{body}')
            issues.append({'kind': 'unverified_quote', 'time': original_time, 'text': body})
            continue

        def check(match):
            value = match.group(1)
            if value in allowed:
                return match.group(0)
            issues.append({'kind': 'unverified_time', 'time': value})
            return f'（时间待核对：{value}）'

        output.append(CITATION.sub(check, line))
    points = [line for line in output if re.match(r'^\s*(?:[-*]|\d+[.)、])\s+\S', line)
              and not QUOTE.match(line) and line.strip() not in ('- 未提及', '* 未提及')]
    sourced = sum(any(stamp in allowed for stamp in CITATION.findall(line)) for line in points)
    return {'text': '\n'.join(output), 'issues': issues, 'corrected_quotes': corrected,
            'key_points': len(points), 'points_with_sources': sourced,
            'points_need_sources': len(points) - sourced,
            'unverified_quotes': sum(item['kind'] == 'unverified_quote' for item in issues)}
