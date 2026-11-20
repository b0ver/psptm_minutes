"""Metadata-only baseline/delta planner. No source file contents are opened.
Private manifests must remain outside Git. A plan is NOT a verified backup.
"""
import argparse, hashlib, json, os, stat
from pathlib import PurePosixPath


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def entries(rows):
    out = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 5:
            raise ValueError('invalid manifest row')
        name, mode, size, mtime, target = row
        if not isinstance(name, str) or not name or '\x00' in name:
            raise ValueError('invalid path')
        p = PurePosixPath(name)
        if p.is_absolute() or any(x in ('..', '.') for x in name.split('/')) or str(p) != name:
            raise ValueError('unsafe path')
        if name in out:
            raise ValueError('duplicate path')
        if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (mode, size, mtime)):
            raise ValueError('invalid stat')
        # Fail closed: restore cannot follow links or recreate devices/FIFOs.
        if target is not None or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('unsupported file type')
        out[name] = row
    for name in out:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and (str(parent) not in out or not stat.S_ISDIR(out[str(parent)][1])):
                raise ValueError('missing or non-directory ancestor')
    return out


def plan(old_rows, new_rows, limit=256*1024*1024):
    if limit <= 0:
        raise ValueError('invalid limit')
    old, new = entries(old_rows), entries(new_rows)
    changed = [new[k] for k in sorted(new) if old.get(k) != new[k]]
    files = [r for r in changed if stat.S_ISREG(r[1])]
    packs, pack, total = [], [], 0
    for row in files:
        if pack and total + row[2] > limit:
            packs.append(pack); pack, total = [], 0
        pack.append(row); total += row[2]
    if pack:
        packs.append(pack)
    result = {'schema': 1, 'status': 'PLANNED_NOT_VERIFIED',
              'baseline_manifest_sha256': digest(old_rows), 'target_manifest_sha256': digest(new_rows),
              'metadata_only_equality_not_content_proof': True,
              'deleted': sorted(set(old)-set(new), key=lambda p: (-p.count('/'), p)),
              'directories': [r for r in changed if stat.S_ISDIR(r[1])],
              'packages': [{'index': i, 'rows': p, 'logical_bytes': sum(r[2] for r in p),
                            'selection_sha256': digest(p)} for i, p in enumerate(packs)],
              'logical_delta_bytes': sum(r[2] for r in files)}
    result['plan_sha256'] = digest(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline'); parser.add_argument('target'); parser.add_argument('output')
    args = parser.parse_args()
    with open(args.baseline) as f: old = json.load(f)
    with open(args.target) as f: new = json.load(f)
    result = plan(old, new)
    # Never overwrite an immutable plan, even on a retry.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(canonical(result)); f.flush(); os.fsync(f.fileno())
    print(json.dumps({'status': result['status'], 'packages': len(result['packages']),
                      'delta_bytes': result['logical_delta_bytes'], 'plan_sha256': result['plan_sha256']}))
