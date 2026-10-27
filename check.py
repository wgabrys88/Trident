"""Safe, isolated component checks: no wake, desktop input or memory wipe."""
import argparse, json, os, sys, time, unittest
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--record', type=Path)
    args = parser.parse_args()
    os.environ['TRIDENT_JOURNAL'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    body = dict(command='python -X utf8 -B check.py', seconds=time.perf_counter() - started,
                tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                exit_code=0 if result.wasSuccessful() else 1, full_wakes=0, desktop_input_events=0)
    print(json.dumps(body, indent=2))
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(body, indent=2), encoding='utf-8')
    sys.exit(body['exit_code'])


if __name__ == '__main__':
    main()
