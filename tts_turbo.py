from __future__ import annotations
import argparse, json, socket, struct, subprocess, sys, time, wave
from pathlib import Path

from main import ROOT, _port_in_use, _kill_port, install_tts

RUNTIME = ROOT / "tools/runtime/tts"
MODELS = ROOT / "models"
VOICE = ROOT / "data/ref-trump.wav"
LANGUAGE = "en"
TIMESTAMP_FORMAT = "%d-%m-%y-%H-%M-%S"
T3 = MODELS / "chatterbox-t3-turbo-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-turbo-q4_0-rawf32-v1.gguf"
KNOBS = {"n-gpu-layers": 99, "context": 8196, "threads": 4, "fastconv": 1, "seed": 42,
         "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0,
         "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 2,
         "cfg-weight": 0, "exaggeration": 0}
RATE, PORT = 24000, 17935
CHUNKER = ROOT / "tools/runtime/chunker/Scripts/python.exe"
MAGIC, VERSION = 0x32525454, 2
REQUEST, RESPONSE = struct.Struct("<7I"), struct.Struct("<8I")
LOG = ROOT / ".runtime-logs/tts.log"


def _command() -> list:
    cmd = [str(RUNTIME / "chatterbox-server.exe"), "--run-id", "turbo", "--family", "turbo",
           "--model", str(T3), "--s3gen-gguf", str(S3), "--reference", str(VOICE),
           "--language", LANGUAGE, "--port", str(PORT)]
    cmd.extend(arg for n, v in KNOBS.items() for arg in (f"--{n}", str(v)))
    return cmd

class TTS:
    _proc: subprocess.Popen = None
    _log_path: Path = LOG
    _log_fh = None

    def _emit(self, msg: str) -> None:
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()

    def start(self) -> TTS:
        if self._proc is not None and self._proc.poll() is None:
            return self
        if _port_in_use(PORT):
            self._emit("start | port already listening")
            return self
        exe = RUNTIME / "chatterbox-server.exe"
        self._emit(f"start | spawning server")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = self._log_path.open("ab", buffering=0)
        cmd = _command()
        pre_size = self._log_fh.tell()
        self._proc = subprocess.Popen(cmd, cwd=RUNTIME, stdin=subprocess.DEVNULL,
                                      stdout=self._log_fh, stderr=self._log_fh)
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(0.5)
            if self._proc.poll() is not None:
                raise RuntimeError(f"server died with code {self._proc.poll()}")
            self._log_fh.flush()
            with open(self._log_path, "r", encoding="utf-8", errors="replace") as lf:
                lf.seek(pre_size)
                for line in lf:
                    if " server.ready" in line and "| turbo" in line:
                        self._emit("start | ready")
                        return self
        self._proc.kill()
        raise TimeoutError("server startup timed out")

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
        _kill_port(PORT)

    def synthesize(self, text: str) -> Path:
        self.start()
        pieces = self._chunks(text)
        output = ROOT / f"out_{time.strftime(TIMESTAMP_FORMAT)}_tts.wav"
        with socket.create_connection(("127.0.0.1", PORT), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for piece_id, piece in enumerate(pieces):
                self._send(sock, 1, piece_id, piece)
            pcm_bytes = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                for piece_id in range(len(pieces)):
                    before = pcm_bytes
                    while True:
                        kind, returned_piece, chunk, payload = self._receive(reader)
                        if returned_piece != piece_id:
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        zeros = RATE // 50 * 2
                        if piece_id == 0 and chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                            payload = payload[zeros:]
                        wav.writeframesraw(payload)
                        pcm_bytes += len(payload)
                    if pcm_bytes == before:
                        raise RuntimeError(f"TTS piece {piece_id} produced no audio")
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        return output

    @staticmethod
    def _chunks(text: str) -> list:
        if not CHUNKER.is_file():
            raise RuntimeError("CPU chunker not installed")
        proc = subprocess.run([str(CHUNKER), str(ROOT / "chunk.py")], input=text,
                              capture_output=True, text=True, encoding="utf-8")
        if proc.stderr:
            sys.stderr.write(proc.stderr)
            sys.stderr.flush()
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or "CPU chunker failed")
        pieces = json.loads(proc.stdout)
        if not pieces:
            raise ValueError("TTS input is empty")
        return pieces

    @staticmethod
    def _send(sock, kind: int, piece: int = 0, text: str = "") -> None:
        payload = text.encode("utf-8")
        sock.sendall(REQUEST.pack(MAGIC, VERSION, kind, 0, 0, piece, len(payload)) + payload)

    @staticmethod
    def _receive(reader) -> tuple:
        header = reader.read(RESPONSE.size)
        if len(header) != RESPONSE.size:
            raise EOFError("Native TTS closed the connection")
        magic, version, kind, epoch, response, piece, chunk, length = RESPONSE.unpack(header)
        if (magic, version) != (MAGIC, VERSION) or (kind != 5 and (epoch, response) != (0, 0)):
            raise RuntimeError("Unexpected TTS response header")
        payload = reader.read(length)
        if len(payload) != length:
            raise EOFError("Incomplete native TTS audio frame")
        if kind == 4:
            raise RuntimeError(payload.decode("utf-8", errors="replace"))
        return kind, piece, chunk, payload

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    p.add_argument("--load", action="store_true")
    p.add_argument("--unload", action="store_true")
    text = p.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    args = p.parse_args()
    tts = TTS()
    if args.install:
        install_tts("turbo", T3, S3)
        tts.start()
        sys.exit(0)
    if args.load:
        install_tts("turbo", T3, S3)
        tts.start()
        print("[tts] ready", flush=True)
        input()
        tts.stop()
        sys.exit(0)
    if args.unload:
        tts.stop()
        sys.exit(0)
    src = (args.text if args.text is not None else
           (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
           (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    t_start = time.perf_counter()
    tts.start()
    t_synth = time.perf_counter()
    wav = tts.synthesize(src)
    t_done = time.perf_counter()
    (ROOT / "tts_out.wav").write_bytes(wav.read_bytes())
    print(wav)
    wav_info = wave.open(str(wav))
    duration_s = wav_info.getnframes() / wav_info.getframerate()
    wav_info.close()
    print(f"[rtf] tts_start={t_synth-t_start:.3f}s", file=sys.stderr)
    print(f"[rtf] tts_synth={t_done-t_synth:.3f}s", file=sys.stderr)
    print(f"[rtf] tts_total={t_done-t_start:.3f}s", file=sys.stderr)
    print(f"[rtf] audio_s={duration_s:.3f}s", file=sys.stderr)
