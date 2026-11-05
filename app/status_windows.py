"""Lossless source windows. This module never decides whether work is active."""
from bisect import bisect_right
import re


class SourceWindowError(ValueError):
    pass


def _span(row, begin, end, ordinal):
    result = {'source_id': row['id'], 'span_ref': f'S{ordinal:06d}',
              'start_char': begin, 'end_char': end,
              'start': row['start'], 'end': row['end'], 'text': row['text'][begin:end]}
    if 'speaker_hint' in row:
        result['speaker_hint'] = row['speaker_hint']
    return result


def verify_plan(source, windows, fits):
    if not source or not windows or any(not window or not fits(window) for window in windows):
        raise SourceWindowError('source_status_invalid_or_over_budget_window')
    if len({row['id'] for row in source}) != len(source):
        raise SourceWindowError('source_status_duplicate_source_identity')
    flat = [span for window in windows for span in window]
    row_index, cursor = 0, 0
    seen = set()
    for ordinal, span in enumerate(flat, 1):
        if row_index >= len(source):
            raise SourceWindowError('source_status_extra_source_span')
        row = source[row_index]
        if (span['span_ref'] in seen or span['span_ref'] != f'S{ordinal:06d}'
                or span != _span(row, cursor, span['end_char'], ordinal)
                or not cursor <= span['end_char'] <= len(row['text'])
                or (span['end_char'] == cursor and row['text'])):
            raise SourceWindowError('source_status_source_span_changed_or_missing')
        seen.add(span['span_ref'])
        cursor = span['end_char']
        if cursor == len(row['text']):
            row_index += 1
            cursor = 0
    if row_index != len(source) or cursor:
        raise SourceWindowError('source_status_source_tail_missing')
    return True


def plan_windows(source, fits, maximum_windows):
    """Use actual final-packet admission through fits; never trim a source word."""
    if not source or type(maximum_windows) is not int or maximum_windows < 1:
        raise SourceWindowError('source_status_invalid_window_plan')
    if any(not isinstance(row.get('text'), str) or not isinstance(row.get('id'), str)
           or not row['id'] for row in source) or len({row['id'] for row in source}) != len(source):
        raise SourceWindowError('source_status_invalid_source_identity')
    windows, window, ordinal = [], [], 1

    def emit():
        nonlocal window
        if window:
            windows.append(window)
            window = []
            if len(windows) > maximum_windows:
                raise SourceWindowError('source_status_window_budget_exceeded')

    for row in source:
        cursor = 0
        boundaries = [match.end() for match in re.finditer(r'\s+', row['text'])]
        if not boundaries or boundaries[-1] != len(row['text']):
            boundaries.append(len(row['text']))
        while True:
            whole = _span(row, cursor, len(row['text']), ordinal)
            if fits(window + [whole]):
                window.append(whole)
                ordinal += 1
                break
            if window:
                emit()
                continue
            start_index = bisect_right(boundaries, cursor)
            lo, hi, chosen = start_index, len(boundaries) - 1, None
            while lo <= hi:
                middle = (lo + hi) // 2
                end = boundaries[middle]
                part = _span(row, cursor, end, ordinal)
                if fits([part]):
                    chosen = part
                    lo = middle + 1
                else:
                    hi = middle - 1
            if chosen is None or not fits([chosen]):
                raise SourceWindowError('source_status_indivisible_source_word_exceeds_budget')
            window.append(chosen)
            ordinal += 1
            cursor = chosen['end_char']
            emit()
            if cursor == len(row['text']):
                break
    emit()
    verify_plan(source, windows, fits)
    return windows
