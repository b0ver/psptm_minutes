"""Hold queue table locks across worker stop to eliminate check/stop races."""
import subprocess
import sys
import select
from pathlib import Path

directory, project = sys.argv[1:]
command = ['docker', 'compose', '--project-directory', directory, '-p', project]
for filename in ('compose.yaml', 'compose.gpu.yaml', 'compose.telemost.yaml', 'compose.release.yaml'):
    command += ['-f', directory + '/' + filename]
for profile in ('ai', 'telegram', 'calendar', 'telemost'):
    command += ['--profile', profile]
code = Path(__file__).with_name('queue_guard.py').read_text()
guard = subprocess.Popen(command + ['exec', '-T', 'api', 'python', '-u', '-c', code],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
try:
    available, _, _ = select.select([guard.stdout], [], [], 20)
    if not available or guard.stdout.readline().strip() != 'idle_locked':
        raise SystemExit('Workers busy or queue lock unavailable; deployment aborted')
    subprocess.run(command + ['stop', 'worker', 'analysis-worker'], check=True, timeout=60)
finally:
    try:
        guard.communicate(input='\n', timeout=10)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        guard.kill()
        guard.wait()
if guard.returncode:
    raise SystemExit('Queue guard failed')
