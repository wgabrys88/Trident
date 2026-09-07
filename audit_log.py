"""One lossless diagnostic log bundle per synthesis. No tensor values in console output."""
import hashlib
import json
import re
import struct
from pathlib import Path
import zipfile


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pack(path: Path, metadata: dict, native_log: str, prefix: Path | None) -> None:
    artifacts = sorted(prefix.parent.glob(prefix.name + '.*')) if prefix else []
    manifest = []
    # Exclusive creation prevents an earlier log from silently becoming this run's evidence.
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        bundle.writestr('run.json', json.dumps(metadata, ensure_ascii=False, indent=2))
        bundle.writestr('native.log', native_log)
        for file in artifacts:
            if not file.is_file() or file.is_symlink():
                raise ValueError(f'invalid audit artifact: {file}')
            manifest.append({'file': file.name, 'bytes': file.stat().st_size, 'sha256': sha256(file)})
            bundle.write(file, file.name)
        bundle.writestr('manifest.json', json.dumps({'schema': 1, 'artifacts': manifest}))
    # Keep loose evidence if packing fails. Delete only the exact successfully archived files.
    for file in artifacts:
        file.unlink()


def unpack(path: Path, target: Path) -> dict:
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or any(Path(name).name != name for name in names):
            raise ValueError('duplicate or non-flat log entry')
        metadata = json.loads(bundle.read('run.json'))
        manifest = json.loads(bundle.read('manifest.json'))
        if manifest['schema'] != 1:
            raise ValueError('unsupported manifest version')
        expected = [item['file'] for item in manifest['artifacts']]
        if len(expected) != len(set(expected)) or not set(expected) <= set(names):
            raise ValueError('incomplete artifact manifest')
        for item in manifest['artifacts']:
            data = bundle.read(item['file'])
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError(f"artifact integrity failed: {item['file']}")
            (target / item['file']).write_bytes(data)
        (target/'native.log').write_bytes(bundle.read('native.log'))
    return metadata


def append_report(path: Path, report: dict, command: str, asr: dict) -> None:
    with zipfile.ZipFile(path, 'a', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        if 'report.json' in bundle.namelist():
            raise ValueError('validation report already exists')
        bundle.writestr('report.json', json.dumps(report, ensure_ascii=False, indent=2))
        bundle.writestr('command.log', command)
        bundle.writestr('asr.json', json.dumps(asr, ensure_ascii=False))


def compare_runs(first: Path, second: Path) -> dict:
    """Exact repeatability comparison, never an independent correctness reference."""
    def ordered(bundle):
        roots = sorted((name for name in bundle.namelist() if re.search(r'\.r[1-9][0-9]*_p[0-9]+\.audit\.jsonl$',name)),
                       key=lambda name: tuple(map(int,re.search(r'\.r(\d+)_p(\d+)',name).groups())))
        for root in roots:
            prefix = root.removesuffix('.audit.jsonl')
            for event in map(json.loads,bundle.read(root).splitlines()):
                if event['event']=='t3.decision':
                    step = prefix+f".t3-s{event['step']}.audit.jsonl"
                    yield from (e for e in map(json.loads,bundle.read(step).splitlines()) if e['event']=='tensor')
                if event['event']=='tensor':
                    yield event
    with zipfile.ZipFile(first) as left, zipfile.ZipFile(second) as right:
        a, b = (json.loads(bundle.read('run.json')) for bundle in (left,right))
        for key in ('family','source','settings','language','native_pin','provenance','python_sources'):
            if key not in a or a[key] != b.get(key):
                return {'status':'INCONCLUSIVE','reason':f'run inputs/provenance differ: {key}'}
        xs, ys = list(ordered(left)), list(ordered(right))
        if not xs or len(xs)!=len(ys):
            return {'status':'DIVERGENCE','reason':'different or missing event counts','counts':[len(xs),len(ys)]}
        for x,y in zip(xs,ys):
            if any(x[k]!=y[k] for k in ('name','dtype','shape')):
                return {'status':'DIVERGENCE','reason':'stage layout differs','expected':x,'actual':y}
            expected, actual = left.read(x['file']), right.read(y['file'])
            if expected == actual:
                continue
            size, fmt = {'f32':(4,'f'),'f64':(8,'d'),'i32':(4,'i')}[x['dtype']]
            if len(expected)!=len(actual):
                return {'status':'DIVERGENCE','stage':x['name'],'reason':'tensor byte count differs'}
            offset = next(i for i in range(0,len(expected),size) if expected[i:i+size]!=actual[i:i+size])
            index, coords = offset//size, []
            for dim in reversed(x['shape']):
                coords.insert(0,index%dim); index//=dim
            return {'status':'DIVERGENCE','stage':x['name'],'file':x['file'],'coordinates':coords,
                    'expected':struct.unpack_from('<'+fmt,expected,offset)[0],
                    'actual':struct.unpack_from('<'+fmt,actual,offset)[0],
                    'meaning':'first captured bitwise divergence between repeated native runs; not proof of speech cause'}
        return {'status':'EXACT_MATCH','tensor_count':len(xs),'meaning':'repeatability only; reference correctness remains INCONCLUSIVE'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Read a compact report without loading tensor data')
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.log) as bundle:
        report = json.loads(bundle.read('report.json' if 'report.json' in bundle.namelist() else 'run.json'))
    semantic = report.get('semantic', {})
    print(json.dumps({key: report[key] for key in ('case','status','speech_status','reference_status','cause_identified','errors','structural_errors') if key in report} |
                     {'transcript': semantic.get('transcript'), 'edit_distance': semantic.get('edit_distance')}, ensure_ascii=False, indent=2))
