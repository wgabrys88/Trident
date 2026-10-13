import asyncio
from fabric import CFG, ROOT, serve

async def main():
    from faster_whisper import WhisperModel
    recognizer = await asyncio.to_thread(WhisperModel, CFG['asr_model'], device='cpu', compute_type='int8',
                                        download_root=str(ROOT/'runtime'/'models'/'whisper'))
    def transcribe(path):
        segments, _ = recognizer.transcribe(path, beam_size=1, vad_filter=True)
        return dict(text=' '.join(s.text for s in segments).strip())
    async def handle(op, args):
        return await asyncio.to_thread(transcribe, args['path'])
    await serve(handle, 'cochlea')

if __name__ == '__main__':
    asyncio.run(main())
