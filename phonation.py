import asyncio, os
import soundfile as sf
from fabric import RUN, execute, log, request, serve, token

async def handle(op, args):
    path = RUN / (token() + '.wav')
    script = ("Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList 24000, "
              "([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono); "
              "$v.SetOutputToWaveFile($env:TRIDENT_WAV,$f); $v.Speak($env:TRIDENT_TEXT); $v.Dispose()")
    await asyncio.to_thread(execute, ['powershell.exe', '-NoProfile', '-Command', script], 60,
                            env=os.environ | {'TRIDENT_WAV':str(path), 'TRIDENT_TEXT':args['text']})
    samples, rate = sf.read(path)
    note = path.with_suffix('.ogg')
    sf.write(note, samples, rate, format='OGG', subtype='OPUS')
    try:
        result = await request('relay', 'deliver', dict(path=str(note), wav=str(path)), len(samples)/rate+20)
    except Exception as error:
        result = dict(error=f'Voice delivery outcome unknown: {type(error).__name__}: {error}',
                      outcome='unknown', delivered=False, hearing_verified=False)
    return dict(text=args['text'], note=str(note), **result)

if __name__ == '__main__':
    asyncio.run(serve(handle, 'phonation'))
