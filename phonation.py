import asyncio, os
import soundfile as sf
from fabric import RUN, execute, request, serve, token

async def handle(op, args):
    path = RUN / (token() + '.wav')
    script = ("Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList 24000, "
              "([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono); "
              "$v.SetOutputToWaveFile($env:TRIDENT_WAV,$f); $v.Speak($env:TRIDENT_TEXT); $v.Dispose()")
    await asyncio.to_thread(execute, ['powershell.exe', '-NoProfile', '-Command', script], 20,
                            env=os.environ | {'TRIDENT_WAV':str(path), 'TRIDENT_TEXT':args['text']})
    samples, rate = sf.read(path)
    note = path.with_suffix('.ogg')
    sf.write(note, samples, rate, format='OGG', subtype='OPUS')
    result = await request('relay', 'deliver', dict(path=str(note)), 15)
    return dict(note=str(note), **result)

if __name__ == '__main__':
    asyncio.run(serve(handle, 'phonation'))
