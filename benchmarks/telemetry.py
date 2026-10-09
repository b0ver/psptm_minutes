import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ollama_rss():
    total = 0
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if not directory.joinpath('comm').read_text().strip().startswith('ollama'):
                continue
            status = directory.joinpath('status').read_text().splitlines()
            total += next(int(line.split()[1]) for line in status if line.startswith('VmRSS:')) * 1024
        except (OSError, StopIteration, ValueError):
            pass
    return total


def main():
    while True:
        try:
            progress = json.loads((ROOT / 'progress.json').read_text())
            gate = json.loads((ROOT / 'gate.json').read_text())
            sample = {'time': time.time(), 'phase': progress['phase'], 'model': progress.get('model'), 'stage': progress.get('stage'), 'ollama_host_rss_bytes': ollama_rss(), 'gpu_memory_mib': gate.get('memory_mib'), 'gpu_utilization': gate.get('gpu_utilization')}
            with (ROOT / 'telemetry.jsonl').open('a') as output:
                output.write(json.dumps(sample) + '\n')
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(5)


if __name__ == '__main__':
    main()
