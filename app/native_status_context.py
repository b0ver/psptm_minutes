import json
import hashlib
import unicodedata
from pathlib import Path

from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1] / 'config' / 'native-context-9b'
CHAT_ENVELOPE_ALLOWANCE = 4096
ENGINES = {}


def engines(name):
    if name not in ENGINES:
        if name not in ('qwen3.5:9b',):
            raise ValueError('tokenizer_model_not_validated')
        directory = ROOT / name.replace(':', '-')
        metadata = next(row for row in json.loads((ROOT / 'derivation-summary.json').read_text())['models'] if row['model'] == name)
        result = []
        for variant in ('hf-regex', 'qwen35-regex'):
            path = directory / ('native-' + variant + '.json')
            expected = next(row['sha256'] for row in metadata['variants'] if row['variant'] == variant)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('tokenizer_asset_changed')
            tokenizer = Tokenizer.from_file(str(path))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            if not hasattr(tokenizer, 'encode_special_tokens'):
                raise ValueError('special_token_counting_mode_unavailable')
            result.append(tokenizer)
        ENGINES[name] = result
    return ENGINES[name]


def content_tokens(name, text):
    candidates = {text, unicodedata.normalize('NFC', text)}
    counts = []
    for tokenizer in engines(name):
        for special in (False, True):
            tokenizer.encode_special_tokens = special
            for candidate in candidates:
                counts.append(len(tokenizer.encode(candidate, add_special_tokens=False).ids))
    return max(counts, default=0)


def admission(name, system, user, output_budget, context):
    if output_budget <= 0 or context <= 0:
        raise ValueError('invalid_context_budget')
    count = content_tokens(name, system) + content_tokens(name, user)
    total = count + CHAT_ENVELOPE_ALLOWANCE + output_budget
    return {'native_content_token_envelope': count, 'chat_envelope_allowance': CHAT_ENVELOPE_ALLOWANCE, 'output_budget': output_budget, 'context': context, 'required_envelope': total, 'admitted': total <= context, 'truncation_enabled': False}
