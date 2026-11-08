"""Keep summary text, provenance and review state in the same revision."""
import copy
import hashlib
import json

FIELDS = ('summary_original', 'summary_validation', 'summary_inference',
          'summary_engine', 'summary_review', 'summary_source', 'summary_style')


def source_digest(document):
    payload = {key: document.get(key) for key in ('segments', 'speakers')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def snapshot(document):
    return {'text': document.get('summary', ''), 'stale': document.get('summary_stale', False),
            'metadata': {key: copy.deepcopy(document[key]) for key in FIELDS if key in document}}


def restore(document, previous):
    current = snapshot(document)
    for key in FIELDS:
        document.pop(key, None)
    document.update(copy.deepcopy(previous.get('metadata', {})))
    document['summary'] = previous['text']
    document['previous_summary'] = current
    document['summary_stale'] = True


def mark_edited(document):
    from summary_validation import validate_summary
    checked = validate_summary(document['summary'], document)
    checked.pop('text')
    document['summary_validation'] = checked
    document['summary_review'] = {'status': 'edited'}
