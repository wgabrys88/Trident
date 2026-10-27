"""Opt-in live Fara replay of two questions on a saved bitmap. Never sends input."""
import argparse, hashlib, json, os, shutil, sys, tempfile, time, urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from base import configure
from desk import Desk, unpng
from eye import ask

QUESTIONS = {
    'fact': 'Is a round cyan sky-blue color swatch drawn in the top row of the Colors section? Do not click. Terminate and answer only yes or no.',
    'click': 'In this Paint window, click the center of the round cyan sky-blue color swatch in the top row of the Colors section. If it is not drawn, terminate and say it is not visible.',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vision-url', default='http://192.168.16.31:1234')
    parser.add_argument('--vision-model', default='retina')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['TRIDENT_JOURNAL'] = '1'
    data = args.image.read_bytes()
    image = unpng(data)
    screen = dict(id=args.image.stem, image=str(args.image.resolve()), size=list(image[1:]), region='297,0,1000,944', sha256=hashlib.sha256(data).hexdigest())
    results = []
    with tempfile.TemporaryDirectory(prefix='trident-replay-') as folder:
        work = Path(folder)
        configure(work, 'saved-replay', vision_url=args.vision_url, vision_model=args.vision_model)
        app = SimpleNamespace(work=work, desk=Desk(work))
        try:
            with urllib.request.urlopen(args.vision_url + '/v1/models', timeout=10) as response:
                metadata = json.load(response)
            (args.output / 'models.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        except Exception as error:
            (args.output / 'models.json').write_text(json.dumps(dict(error=str(error))), encoding='utf-8')
        for name, question in QUESTIONS.items():
            started = time.perf_counter()
            try:
                result, exit_code = ask(app, image, question, screen), 0
            except Exception as error:
                result, exit_code = dict(error=str(error)), 1
            row = dict(experiment=name, question=question, seconds=time.perf_counter() - started, exit_code=exit_code,
                       result=result, pinned=screen, actions_executed=0)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        for file in work.glob('*.jsonl'):
            shutil.copyfile(file, args.output / file.name)
    (args.output / 'replays.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(max(row['exit_code'] for row in results))


if __name__ == '__main__':
    main()
