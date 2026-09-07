"""Lossless capture storage and streaming access; no ASR or inference."""
import hashlib
import json
import struct
from pathlib import Path
import zipfile


def pack(path: Path, metadata: dict, stderr: bytes, capture: Path | None) -> None:
    manifest = []
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        for name, data in [('run.json', json.dumps(metadata, ensure_ascii=False).encode('utf-8')),
                           ('stderr.txt', stderr)]:
            bundle.writestr(name, data)
            manifest.append({'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        if capture and (metadata['status'] == 'CAPTURED' or capture.is_file()):
            digest = hashlib.sha256()
            size = 0
            with capture.open('rb') as src, bundle.open('capture.bin', 'w', force_zip64=True) as dst:
                while block := src.read(4 << 20):
                    digest.update(block)
                    size += len(block)
                    dst.write(block)
            manifest.append({'file': 'capture.bin', 'bytes': size, 'sha256': digest.hexdigest()})
        bundle.writestr('manifest.json', json.dumps({'schema': 2, 'artifacts': manifest}))
    if capture:
        capture.unlink(missing_ok=True)


def records(stream, payloads: bool = False):
    """Yield (header, raw bytes); default skips payloads with bounded memory."""
    while frame := stream.read(12):
        header_size, data_size = struct.unpack('<IQ', frame)
        header = json.loads(stream.read(header_size))
        if header['schema'] != 2:
            raise ValueError('unsupported capture schema')
        if payloads:
            data = stream.read(data_size)
            if len(data) != data_size:
                raise EOFError('truncated capture tensor')
        else:
            data = None
            remaining = data_size
            while remaining:
                block = stream.read(min(remaining, 4 << 20))
                if not block:
                    raise EOFError('truncated capture tensor')
                remaining -= len(block)
        yield header, data


def tensor(header: dict, data: bytes):
    """Optional NumPy view of a recorded tensor; preserves values and repetitions."""
    import numpy as np
    dtype = {'f16': '<f2', 'f32': '<f4', 'f64': '<f8', 'i32': '<i4', 'i16': '<i2', 'utf8': 'u1'}
    return np.frombuffer(data, dtype=dtype[header['dtype']]).reshape(header['shape'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Read capture metadata without loading tensor data')
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.bundle) as bundle:
        run = json.loads(bundle.read('run.json'))
        manifest = json.loads(bundle.read('manifest.json'))
    print(json.dumps({'status': run['status'], 'family': run['family'],
                      'source': run['source'], 'native_pin': run['native_pin'],
                      'error': run.get('error'), 'artifacts': manifest['artifacts']}, indent=2))
