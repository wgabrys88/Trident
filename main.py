import importlib.util, sys, time
from pathlib import Path

from base import CODE, Fail, command, configure, emit, log, serve_journal
from decide import decide
from desk import Desk, hand
from eye import eye
from memory import learn_tool, ledger, recall_tool, stop, story, wipe
from mind import mind
from measure import measure
from state import atomic, locked


class Capture:
    name, flag = 'capture', 'capture'
    doc = '''$CALL --capture
Takes a capture of the region and writes shots/<id>.png and screen.json. Use Trident's Fara eye to perceive the capture; do not open it with native image tools.'''

    def run(self, app, _arg, dry=False):
        if dry:
            return dict(dry=True)
        return app.desk.capture()


class Windows:
    name, flag = 'windows', 'windows'
    doc = '''$CALL --windows
Lists visible titled windows, top of the z-order first. A minimized window has box null. Use the hwnd with --place.'''

    def run(self, app, _arg, dry=False):
        return dict(windows=app.desk.windows())


class Region:
    name, flag = 'region', 'region'
    doc = '''$CALL --region NONE
or --region LEFT,TOP,RIGHT,BOTTOM on a 0-1000 screen. Later captures, the eye, and the hand use it. Hand points are 0-1000 of that box.'''

    def run(self, app, value, dry=False):
        value = str(value or 'NONE').strip()
        if value != 'NONE':
            parts = value.split(',')
            nums = list(map(int, parts)) if len(parts) == 4 else []
            if not (len(nums) == 4 and 0 <= nums[0] < nums[2] <= 1000 and 0 <= nums[1] < nums[3] <= 1000):
                raise Fail('region is NONE or left,top,right,bottom on 0-1000')
        if dry:
            return dict(dry=True, region=value)
        with locked(app.work / 'capture.lock'):
            atomic(app.work / 'region.txt', value)
        return dict(region=value)


class Place:
    name, flag = 'place', 'place'
    doc = '''$CALL --place HWND LEFT TOP RIGHT BOTTOM
Moves a window from --windows to that 0-1000 box and clears topmost. Capture afterwards.'''

    def run(self, app, values, dry=False):
        if dry:
            return dict(dry=True, place=list(values))
        return app.desk.place(*values)


BUILTIN = [eye, decide, hand, story, ledger, Capture(), Windows(), Region(), Place(), learn_tool, recall_tool, stop, mind, measure]
BLOCKED = {'eye', 'decide', 'hands', 'capture', 'windows', 'region', 'place', 'measure'}


class App:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.desk = Desk(self.work)


def load_skills():
    found = {}
    folder = CODE / 'skills'
    if not folder.exists():
        return found
    for path in sorted(folder.glob('*.py')):
        if path.stem.startswith('_'):
            continue
        if path.stem in {tool.name for tool in BUILTIN}:
            raise Fail(f'skill {path.stem} shadows a tool')
        spec = importlib.util.spec_from_file_location('skill_' + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found[path.stem] = getattr(module, path.stem)
    return found


def docs_of(skills):
    listed = []
    prefix = command() + '\nEach tool below is that same command plus the flag on its first line.\n'
    first = True
    for tool in BUILTIN:
        text = tool.doc.replace('$CALL ', '')
        if first:
            text = prefix + text
            first = False
        listed.append(text)
    for name, fn in skills.items():
        text = (fn.__doc__ or f'$CALL --{name}\nRuns the skill.').replace('$CALL ', '')
        listed.append(text)
    return listed


def chosen(args, skills):
    picks = []
    for tool in BUILTIN:
        value = getattr(args, tool.flag)
        if tool.flag in ('capture', 'windows', 'learn', 'recall', 'stop'):
            if value:
                picks.append((tool, True))
        elif value not in (None, ''):
            picks.append((tool, value))
    for name, fn in skills.items():
        value = getattr(args, name)
        if value is not None:
            picks.append((fn, value))
    return picks


def main(argv=None):
    import argparse
    skills = load_skills()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--work', default=str(CODE))
    parser.add_argument('--run', default='')
    parser.add_argument('--minutes', default='')
    parser.add_argument('--wake-start', default='')
    parser.add_argument('--wake-end', default='')
    parser.add_argument('--shot-delay', default='1.5')
    parser.add_argument('--vision-url', default='http://192.168.16.31:1234')
    parser.add_argument('--vision-model', default='retina')
    parser.add_argument('--coordinate-mode', choices=('normalized', 'pixels'), default='normalized')
    parser.add_argument('--decide-model', default='typesafe/jev-1.14-20261014')
    parser.add_argument('--mind-model', default='')
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--journal', action='store_true')
    parser.add_argument('--eye-file')
    parser.add_argument('--decide-file')
    parser.add_argument('--hands-file')
    parser.add_argument('--story-file')
    parser.add_argument('--ledger-file')
    parser.add_argument('--mind-file')
    parser.add_argument('--measure-file')
    parser.add_argument('--region')
    parser.add_argument('--place', nargs=5)
    for tool in BUILTIN:
        if tool.flag in ('capture', 'windows', 'learn', 'recall', 'stop'):
            parser.add_argument('--' + tool.flag, action='store_true')
    for name in skills:
        parser.add_argument('--' + name, nargs='*', default=None)
    args = parser.parse_args(argv)
    configure(args.work, args.run, args.minutes, args.wake_start, args.wake_end, args.shot_delay, args.vision_url, args.vision_model, args.decide_model, args.mind_model, args.coordinate_mode)
    if args.journal:
        serve_journal()
        return
    app = App(args.work)
    if args.fresh:
        wiped = wipe(app.work)
        if not any(getattr(args, tool.flag) not in (None, False, '') for tool in BUILTIN) and not any(getattr(args, name) is not None for name in skills):
            emit('fresh', wiped)
            return
    picks = chosen(args, skills)
    if len(picks) != 1:
        raise Fail('choose one tool flag')
    tool, value = picks[0]
    if (app.work / 'stop').exists() and getattr(tool, 'name', '') in BLOCKED:
        raise Fail('stopped')
    started = time.time()
    try:
        if getattr(tool, 'name', None) == 'mind':
            result = tool.run(app, value, dry=args.dry, docs_of=lambda: docs_of(load_skills()))
        elif getattr(tool, 'run', None):
            result = tool.run(app, value, dry=args.dry)
        else:
            result = tool(app, *value)
    except Exception as error:
        from base import event
        event('component_error', dict(component=getattr(tool, 'name', 'skill'), error=str(error)), seconds=time.time() - started)
        raise
    if isinstance(result, dict):
        emit(getattr(tool, 'name', 'skill'), result)
    log('exit', f"{getattr(tool, 'name', 'skill')} {time.time() - started:.1f}s ended")


if __name__ == '__main__':
    try:
        main()
    except Fail as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
