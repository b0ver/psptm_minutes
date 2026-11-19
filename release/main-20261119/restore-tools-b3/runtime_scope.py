"""Exact immutable exclusions only for approved ephemeral runtime symlinks."""
import stat
from pathlib import PurePosixPath

def persistent(rows, approved):
    if len({r[0] for r in approved})!=len(approved): raise ValueError('duplicate exclusion')
    exclusions={r[0]:r for r in approved}
    for row in approved:
        p,mode,size,mtime,target=row
        if not stat.S_ISLNK(mode) or not isinstance(target,str): raise ValueError('not symlink')
        parts=p.split('/')
        if any(x in ('','.','..') for x in parts) or parts[0]!='telemost': raise ValueError('unsafe scope')
        if parts[-2]=='pulse' and parts[-1].endswith('-runtime'):
            if not target.startswith('/tmp/pulse-') or len(PurePosixPath(target).parts)!=3: raise ValueError('wrong pulse target')
        elif parts[-2]=='browser-profile' and parts[-1] in ('SingletonCookie','SingletonLock','SingletonSocket'):
            if parts[-1]=='SingletonSocket':
                if not target.startswith('/tmp/') or PurePosixPath(target).name!='SingletonSocket' or '..' in PurePosixPath(target).parts: raise ValueError('wrong socket target')
            elif '/' in target or not target or target in ('.','..'): raise ValueError('wrong singleton target')
        else: raise ValueError('not approved runtime category')
    seen=set();result=[]
    for row in rows:
        if row[0] in exclusions:
            if row!=exclusions[row[0]]: raise ValueError('excluded entry changed')
            seen.add(row[0])
        else:result.append(row)
    if seen!=set(exclusions): raise ValueError('excluded entry missing')
    return result
