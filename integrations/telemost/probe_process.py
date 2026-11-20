"""Identify this disposable probe's browser by executable, UID and exact profile."""
import os
from pathlib import Path

def browser_roots(profile_argument,executable,proc=Path('/proc')):
    if not profile_argument.startswith('--user-data-dir=/'):
        raise ValueError('explicit_absolute_browser_profile_required')
    binaries={Path(executable).resolve()}
    if Path(executable)==Path('/usr/bin/chromium'):
        binaries.add(Path('/usr/lib/chromium/chromium'))
    roots=[]
    for entry in proc.iterdir():
        if not entry.name.isdecimal():continue
        try:
            if entry.stat().st_uid!=os.getuid():continue
            command=(entry/'cmdline').read_bytes().split(b'\0')
            actual=(entry/'exe').resolve(strict=True)
        except OSError:continue
        if actual in binaries and profile_argument.encode() in command and not any(arg.startswith(b'--type=') for arg in command):
            roots.append(int(entry.name))
    return roots
