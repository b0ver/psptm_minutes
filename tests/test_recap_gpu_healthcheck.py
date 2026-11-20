"""No Recap deployment or models. Real file locks and exec use synthetic code."""
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ops import recap_gpu_healthcheck as probe


@pytest.mark.parametrize('exit_code', [0, 7])
def test_original_probe_exec_retains_lock_and_exit_code(tmp_path, exit_code):
    lock = tmp_path / 'gpu.lock'; lock.touch(mode=0o400)
    package = tmp_path / 'recap'; package.mkdir(); (package/'__init__.py').touch()
    (package/'__main__.py').write_text('''import fcntl,os,sys
assert sys.argv[1:]==['--smoke','--only','stt']
with open(os.environ['SYNTHETIC_LOCK'],'rb') as f:
 try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError: pass
 else: raise AssertionError('GPU lease lost across exec')
print('synthetic_smoke_retained_lease')
raise SystemExit('''+str(exit_code)+')\n')
    r=subprocess.run([sys.executable,str(Path(probe.__file__).resolve()),str(lock)],
                     env=dict(os.environ,PYTHONPATH=str(tmp_path),SYNTHETIC_LOCK=str(lock)),
                     capture_output=True,text=True)
    assert r.returncode==exit_code and r.stdout.strip()=='synthetic_smoke_retained_lease'
    with lock.open('rb') as f: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_busy_lease_never_executes_model_probe(tmp_path,monkeypatch,capsys):
    lock=tmp_path/'gpu.lock';lock.touch()
    monkeypatch.setattr(probe.os,'execv',lambda *a:pytest.fail('GPU smoke forbidden'))
    monkeypatch.setattr(probe,'liveness',lambda:None)
    with lock.open('rb') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert probe.run(str(lock))==0
    assert json.loads(capsys.readouterr().out)['gpu_readiness']=='deferred_shared_lock'


def test_failed_liveness_is_not_a_healthy_defer(tmp_path,monkeypatch):
    lock=tmp_path/'gpu.lock';lock.touch()
    def fail():raise ValueError('synthetic-detail')
    monkeypatch.setattr(probe,'liveness',fail)
    with lock.open('rb') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):probe.run(str(lock))


@pytest.mark.parametrize('kind',['missing','symlink','directory'])
def test_invalid_lock_never_creates_a_different_lease(tmp_path,monkeypatch,kind):
    target=tmp_path/'target';target.touch();lock=tmp_path/'gpu.lock'
    if kind=='symlink':lock.symlink_to(target)
    if kind=='directory':lock.mkdir()
    monkeypatch.setattr(probe.os,'execv',lambda *a:pytest.fail('GPU smoke forbidden'))
    with pytest.raises((OSError,ValueError)):probe.run(str(lock))
    if kind=='missing':assert not lock.exists()


@pytest.mark.parametrize('body',[b'{"db":true,"ollama":true}',b'{"db":false,"ollama":true}',b'{}',b' '*65537])
def test_liveness_is_bounded_read_only_http(monkeypatch,body):
    monkeypatch.setattr(probe.os,'kill',lambda pid,sig:None)
    def get(url,timeout):
        assert url=='http://bot:8080/health' and timeout==3
        return io.BytesIO(body)
    monkeypatch.setattr(probe.urllib.request,'urlopen',get)
    if body==b'{"db":true,"ollama":true}':probe.liveness()
    else:
        with pytest.raises((KeyError,ValueError)):probe.liveness()
