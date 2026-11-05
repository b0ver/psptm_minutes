"""Run the unchanged frozen screen driver, stopping between generated cells."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/bench')
import compare_smoke
import runner


class CellBoundary(Exception):
    pass


def main():
    atomic = runner.atomic
    budget = compare_smoke.start_model_budget
    generated = []

    def write(path, value):
        atomic(path, value)
        if (Path(path).parent == Path('/bench/private/synthetic')
                and value.get('status') in {'completed', 'failed'}):
            generated.append(value['status'])

    def start_budget(*args, **kwargs):
        if generated:
            raise CellBoundary()
        return budget(*args, **kwargs)

    runner.atomic = write
    compare_smoke.start_model_budget = start_budget
    try:
        compare_smoke.main()
    except CellBoundary:
        pass
    summary = json.loads(Path('/bench/compare-smoke-summary.json').read_text())
    result = {'phase': 'clean_cell_boundary', 'observed': summary['observed'],
              'planned': 4, 'passed': summary.get('passed', False),
              'new_cells': len(generated), 'quality_accepted': False}
    atomic(Path('/bench/screen-batch-progress.json'), result)
    print(json.dumps(result), flush=True)
    return 0 if all(row.get('passed') for row in summary['rows']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
