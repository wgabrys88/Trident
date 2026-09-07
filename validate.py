"""Record two identical real Nano runs on an already built workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from tts_nano import SPEC

ROOT = Path(__file__).resolve().parent
COUNT = (
    'One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. '
    'Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. '
    'Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. '
    'Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. '
    'Twenty-nine. Thirty.'
)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--text', default=COUNT)
    source.add_argument('--text-file', type=Path)
    for name, default in SPEC['knobs'].items():
        parser.add_argument(f'--{name}', type=type(default), default=default)
    args = parser.parse_args()
    text = args.text_file.read_text(encoding='utf-8') if args.text_file else args.text
    settings = {name: getattr(args, name.replace('-', '_')) for name in SPEC['knobs']}
    flags = [part for name, value in settings.items() for part in (f'--{name}', str(value))]
    stamp = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns() % 1_000_000_000:09d}'
    prefix = ROOT / f'nano-validation-{stamp}'
    report_path = Path(f'{prefix}.json')
    prompt_path = Path(f'{prefix}-analyze.txt')
    report = {'schema': 1, 'source': text, 'settings': settings, 'runs': []}

    for number in (1, 2):
        print(f'Nano capture {number}/2 | seed={settings["seed"]}', flush=True)
        for name in ('tts_out.wav', 'tts_out.log.zip'):
            (ROOT / name).unlink(missing_ok=True)
        run = {'number': number, 'status': 'FAILED', 'wav': None, 'bundle': None}
        unload = subprocess.run([sys.executable, str(ROOT / 'tts_nano.py'), '--unload'], cwd=ROOT)
        run['unload_returncode'] = unload.returncode
        if unload.returncode == 0:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tts_nano.py'), '--audit', '--direct',
                 '--text', text, *flags], cwd=ROOT,
            )
            run['returncode'] = result.returncode
            for key, suffix in (('wav', '.wav'), ('bundle', '.log.zip')):
                output = ROOT / f'tts_out{suffix}'
                if output.is_file():
                    saved = Path(f'{prefix}-run{number}{suffix}')
                    output.rename(saved)
                    run[key] = str(saved)
            try:
                if result.returncode != 0:
                    raise ValueError(f'Nano exited with status {result.returncode}')
                if not run['wav'] or not run['bundle']:
                    raise ValueError('Nano did not produce both recording files')
                with zipfile.ZipFile(run['bundle']) as bundle:
                    metadata = json.loads(bundle.read('run.json'))
                    bundle.getinfo('capture.bin')
                if metadata['status'] != 'CAPTURED':
                    raise ValueError(f'Capture status: {metadata["status"]}')
                if metadata['source'] != text or metadata['settings'] != settings:
                    raise ValueError('Recorded input/settings differ from requested input/settings')
                with open(run['wav'], 'rb') as wav:
                    run['wav_sha256'] = hashlib.file_digest(wav, 'sha256').hexdigest()
                if run['wav_sha256'] != metadata['wav_sha256']:
                    raise ValueError('WAV checksum differs from capture metadata')
                run['native_pin'] = metadata['native_pin']
                run['status'] = 'CAPTURED'
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                run['error'] = str(error)
        else:
            run['error'] = 'Nano unload failed; capture was not started'
        report['runs'].append(run)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Run {number}: {run["status"]} {run.get("error", "")}', flush=True)

    complete = all(run['status'] == 'CAPTURED' for run in report['runs'])
    report['status'] = 'CAPTURED' if complete else 'FAILED'
    if complete:
        report['wav_bytes_identical'] = report['runs'][0]['wav_sha256'] == report['runs'][1]['wav_sha256']
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    native = ROOT.parent / 'chatterbox.cpp'
    files = '\n'.join(
        f'Run {run["number"]}: {run["status"]}\n'
        f'WAV: {run["wav"] or "NOT PRODUCED"}\n'
        f'Capture: {run["bundle"] or "NOT PRODUCED"}' for run in report['runs']
    )
    prompt = f'''Analyze these two real, direct Nano recordings of identical text and settings.
Suite report: {report_path}
{files}

Read these source files first:
{ROOT / 'validate.py'}
{ROOT / 'replay.py'}
{ROOT / 'main.py'}
{ROOT / 'tts_nano.py'}
{native / 'REPLAY.md'}
{native / 'src/replay_capture.h'}
{native / 'src/chatterbox_engine.cpp'}
{native / 'src/main.cpp'}
{native / 'src/chatterbox_tts.cpp'}
{native / 'src/s3gen_pipeline.h'}
{native / 'src/server.cpp'}

Use Python; create {ROOT / 'tools/.venv'} and install NumPy there if needed.
Stream ZIP members and binary records using replay.py. Keep derived files flat in
{ROOT}. Do not dump tensors or thousands of log lines into your context.
Verify every manifest member's byte count and SHA-256, the WAV hashes, event
completeness, and recorded model/build/source provenance against the actual files.
Analyze failed captures as failures; never substitute another run's WAV.
CAPTURED means recording completed, not that speech is correct.

Compare corresponding stages across the two runs, separating warmup .r0_p0 from
the actual request .r1_p0. Ignore timestamps/path differences when matching events;
compare tensor payloads exactly first. Report the first difference with record,
scope, stage, tensor coordinates and actual values. Whole ZIP hashes will differ.
Trace exact text, T3 input tokens, full incoming KV caches, positions, raw and
filtered logits, probabilities, RNG states, selected/effective/published tokens,
and forced EOS. Then trace S3 conditioning, speech windows, noise, encoder, CFM,
mel, F0, excitation, waveform, incoming/outgoing state and PCM emission ranges.
Preserve all repetitions. Repeated token IDs alone do not prove repeated words.
Identical seeded runs and identical WAV bytes do not prove correct speech.

Separate proven contract violations, reproducibility differences and audible
content errors. Do not treat ASR as ground truth. Establish a stage's incorrect
transformation only with a compatible pinned independent reference using frozen
inputs and the same weights; this capture workflow does not implement that
reference. Without it, explicitly report what remains unproven. Do not invent
numerical tolerances or claim the repetition's cause from the final transcript.
Return a concise evidence report with exact coordinates and blockers. Do not
modify synthesis, clean the workspace, rebuild, or rerun inference.
'''
    prompt_path.write_text(prompt, encoding='utf-8')
    print(f'\nCapture suite completed: {report["status"]}\nReport: {report_path}\nPrompt: {prompt_path}')
    if complete:
        print(f'WAV bytes identical: {report["wav_bytes_identical"]} (not a speech correctness verdict)')
    print(f'\nCopy this prompt to the analyzing agent:\n\n{prompt}')
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
