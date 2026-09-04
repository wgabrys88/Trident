"""Standalone LLM brain for Trident, powered by llama.cpp + Gemma 4 QAT.

No Python ML packages are required.  All files land under tools/runtime/brain/
and models/ (both gitignored).  Run with:
    python brain.py --request "your prompt"

== Gemma 4 E2B QAT capabilities ==
  - Multimodal: text, image, audio  (use --image / --audio flags to pass files)
  - Native function/tool calling      (pass tools in TOOLS below)
  - Thinking/reasoning mode          (toggle THINKING below)
  - 128K max context
  - 140+ languages
  - Hybrid sliding-window + global attention (sliding window = 512 tokens)

== llama.cpp b10797 hyperparams (all tunable below) ==
  Inference:   temperature, top_p, top_k, min_p, repeat_penalty, mirostat, etc.
  Context:     ctx_size, context_shift, cache_type_k/v, flash_attn, gpu_layers
  Speculative: spec_type, spec_draft_n_max, model_draft, mtp drafter
  Reasoning:   thinking (via <|think|> in system prompt), reasoning_effort/budget
  Output:      json_schema, grammar, simple_io, color, single_turn
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools/runtime/brain"
EXE = RUNTIME / "llama-cli.exe"
MODEL = ROOT / "models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
MTP = ROOT / "models/mtp-gemma-4-E2B-it.gguf"
MTP_CARD = MTP.with_suffix(".md")

LLAMA_REV = "b10797"
ARCHIVE = f"llama-{LLAMA_REV}-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_REV}/{ARCHIVE}"
RUNTIME_SHA = "851a05cb2ed0d35d7b336a193500d3baf86e80983b6d6c024c85afa14b0cadde"
MODEL_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_SHA = "e531007218dfab990486a5de7676a6932d6ea8dea233d1f698d7c21cf8a16889"
MTP_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/mtp-gemma-4-E2B-it.gguf"
MTP_SHA = "586f2460b909008640981ec34060aa864e03c144fbabfb3173c4335087e4aae0"
CARD_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/README.md"
MTP_CARD_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/MTP/README.md"

# ============================================================
# ======================= MODEL ==============================
# ============================================================
SYSTEM_BASE = (
    "You are a helpful, concise assistant. "
    "Generate accurate, well-structured responses."
)

# ============================================================
# ====================== THINKING ===========================
# ============================================================
THINKING = True  # True = reasoning/thinking mode, False = direct answer
# Effort levels: minimal, low, medium, high, xhigh, max  (Gemma 4 native kwarg)
REASONING_EFFORT = "medium"
# Token budget for thinking: -1 = unrestricted, 0 = immediate answer, N = N tokens
REASONING_BUDGET = -1
# Preserve full reasoning trace across multi-turn history (don't strip old thoughts)
REASONING_PRESERVE = True
# Output format for reasoning content:
#   none        - leave thoughts in message.content (raw)
#   deepseek    - move thoughts to message.reasoning_content
#   auto        - detect from template
REASONING_FORMAT = "none"

# ============================================================
# ===================== SAMPLING ============================
# ============================================================
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 64
MIN_P = 0.05
TYPICAL_P = 1.0

# ============================================================
# ================ REPEAT PENALTIES =========================
# ============================================================
REPEAT_LAST_N = 64
REPEAT_PENALTY = 1.0
PRESENCE_PENALTY = 0.0
FREQUENCY_PENALTY = 0.0

# ============================================================
# =================== DYNATEMP / MIROSTAT ==================
# ============================================================
DYNATEMP_RANGE = 0.0
DYNATEMP_EXP = 1.0
MIROSTAT = 0        # 0=disabled, 1=Mirostat, 2=Mirostat 2.0
MIROSTAT_LR = 0.10
MIROSTAT_ENT = 5.00

# ============================================================
# ============== ADAPTIVE / XTC SAMPLING ====================
# ============================================================
ADAPTIVE_TARGET = -1.0
ADAPTIVE_DECAY = 0.90
XTC_PROBABILITY = 0.00
XTC_THRESHOLD = 0.10
DRY_MULTIPLIER = 0.00

# ============================================================
# =================== CONTEXT / KV =========================
# ============================================================
CTX_SIZE = 4096
CONTEXT_SHIFT = False
CACHE_TYPE_K = "f16"    # KV cache K type: f32, f16, bf16, q8_0, q4_0, ...
CACHE_TYPE_V = "f16"    # KV cache V type (same options)
DEFRAG_THOLD = 0.0      # KV defrag threshold (0=disabled)
KV_OFFLOAD = True       # Enable KV cache offloading
GPU_LAYERS = 99         # 99 = all layers on GPU (no CPU offload)

# ============================================================
# =================== PERFORMANCE ===========================
# ============================================================
THREADS = 6
FLASH_ATTN = True       # Flash Attention (on/off/auto)
PERF = True             # Show internal libllama perf timings
WARMUP = True           # Run warmup before first inference
SIMPLE_IO = True        # Basic IO for subprocess / limited console

# ============================================================
# =================== SPEculative / MTP =====================
# ============================================================
SPEC_TYPE = "draft-mtp"    # none, draft-mtp, draft-eagle3, draft-dflash, ...
SPEC_DRAFT_N_MAX = 4       # Max tokens to draft (Gemma 4 MTP: try 4)
SPEC_DRAFT_N_MIN = 0       # Min draft tokens before accepting
SPEC_DRAFT_P_SPLIT = 0.10  # Speculative decoding split probability

# ============================================================
# =============== JSON / GRAMMAR / TOOLS ====================
# ============================================================
# JSON schema string to constrain output (e.g. '{"type":"object"}')
# Leave as "" to disable
JSON_SCHEMA = ""
# BNF grammar file path (leave "" to disable)
GRAMMAR_FILE = ""

# ============================================================
# =================== OUTPUT CONTROL ========================
# ============================================================
COLOR = True            # Colorize output to distinguish prompt from generation
SINGLE_TURN = True      # Single-turn mode: exit after first response
MAX_TOKENS = 2048
RNG_SEED = -1           # -1 = random seed each run

# ============================================================
# =============== MULTIMODAL (image/audio) ==================
# ============================================================
# Pass file paths here as a list, e.g. ["image.png", "audio.wav"]
# Leave empty to disable.
IMAGE_FILES = []
AUDIO_FILES = []
VIDEO_FILES = []

# ============================================================
# ============ INSTALLER (do not change below) ==============
# ============================================================

class BrainInstaller:
    @staticmethod
    def _download(url: str, path: Path, sha: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
            with urllib.request.urlopen(req, timeout=3600) as src, \
                    partial.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if sha:
                with partial.open("rb") as f:
                    digest = hashlib.file_digest(f, "sha256").hexdigest()
                    if digest != sha.lower():
                        raise RuntimeError(
                            f"Checksum mismatch for {path.name}: "
                            f"expected {sha}, got {digest}"
                        )
            partial.replace(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def install(self) -> None:
        runtime_done = (
            EXE.is_file()
            and (RUNTIME / "ggml-vulkan.dll").is_file()
            and (RUNTIME / "llama.dll").is_file()
        )
        model_done = MODEL.is_file() and MODEL_CARD.is_file()
        mtp_done = MTP.is_file() and MTP_CARD.is_file()

        if runtime_done and model_done and mtp_done:
            print("Brain is installed.", file=sys.stderr)
            return

        archive_path = None
        try:
            if not EXE.is_file():
                print(f"Downloading {ARCHIVE}", file=sys.stderr, flush=True)
                archive_path = ROOT / ARCHIVE
                self._download(RUNTIME_URL, archive_path, RUNTIME_SHA)
                RUNTIME.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_path) as zf:
                    for name in zf.namelist():
                        if not name.endswith("/"):
                            (RUNTIME / name).write_bytes(zf.read(name))
                print("llama.cpp runtime installed.", file=sys.stderr)

            if not model_done:
                print(f"Downloading {MODEL.name}", file=sys.stderr, flush=True)
                MODEL.parent.mkdir(parents=True, exist_ok=True)
                self._download(MODEL_URL, MODEL, MODEL_SHA)
                self._download(CARD_URL, MODEL_CARD, "")

            if not mtp_done:
                print(f"Downloading {MTP.name}", file=sys.stderr, flush=True)
                self._download(MTP_URL, MTP, MTP_SHA)
                self._download(MTP_CARD_URL, MTP_CARD, "")

            print("Brain is installed.", file=sys.stderr)
        finally:
            if archive_path and archive_path.is_file():
                archive_path.unlink()


class Brain:
    def _build_system(self) -> str:
        parts = [SYSTEM_BASE]
        if THINKING:
            parts.insert(0, "<|think|>")
        return "\n".join(parts)

    def _build_cmd(self, request: str) -> list[str]:
        system = self._build_system()
        prompt_file = ROOT / "brain_prompt.txt"
        prompt_file.write_text(
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{request}<|im_end|>\n"
            f"<|im_start|>assistant\n",
            encoding="utf-8",
        )
        cmd = [
            str(EXE),
            "-m", str(MODEL),
            "--model-draft", str(MTP),
            "-ngl", str(GPU_LAYERS),
            "-c", str(CTX_SIZE),
            "-t", str(THREADS),
            "--temp", str(TEMPERATURE),
            "--top-p", str(TOP_P),
            "--top-k", str(TOP_K),
            "-n", str(MAX_TOKENS),
            "-f", str(prompt_file),
        ]
        if FLASH_ATTN:
            cmd += ["-fa", "on"]
        if PERF:
            cmd += ["--perf"]
        if not WARMUP:
            cmd += ["--no-warmup"]
        if SIMPLE_IO:
            cmd += ["--simple-io"]
        if COLOR:
            cmd += ["-co", "on"]
        if SINGLE_TURN:
            cmd += ["--single-turn"]
        if not KV_OFFLOAD:
            cmd += ["-nkvo"]
        if CONTEXT_SHIFT:
            cmd += ["--context-shift"]
        if CTX_SIZE and CACHE_TYPE_K != "f16":
            cmd += ["-ctk", CACHE_TYPE_K]
        if CTX_SIZE and CACHE_TYPE_V != "f16":
            cmd += ["-ctv", CACHE_TYPE_V]
        if DEFRAG_THOLD > 0:
            cmd += ["-dt", str(DEFRAG_THOLD)]
        if REPEAT_LAST_N != 64:
            cmd += ["--repeat-last-n", str(REPEAT_LAST_N)]
        if REPEAT_PENALTY != 1.0:
            cmd += ["--repeat-penalty", str(REPEAT_PENALTY)]
        if PRESENCE_PENALTY != 0.0:
            cmd += ["--presence-penalty", str(PRESENCE_PENALTY)]
        if FREQUENCY_PENALTY != 0.0:
            cmd += ["--frequency-penalty", str(FREQUENCY_PENALTY)]
        if MIN_P != 0.05:
            cmd += ["--min-p", str(MIN_P)]
        if TYPICAL_P != 1.0:
            cmd += ["--typical", str(TYPICAL_P)]
        if DYNATEMP_RANGE > 0:
            cmd += ["--dynatemp-range", str(DYNATEMP_RANGE)]
            cmd += ["--dynatemp-exp", str(DYNATEMP_EXP)]
        if ADAPTIVE_TARGET > 0:
            cmd += ["--adaptive-target", str(ADAPTIVE_TARGET)]
            cmd += ["--adaptive-decay", str(ADAPTIVE_DECAY)]
        if XTC_PROBABILITY > 0:
            cmd += ["--xtc-probability", str(XTC_PROBABILITY)]
            cmd += ["--xtc-threshold", str(XTC_THRESHOLD)]
        if DRY_MULTIPLIER > 0:
            cmd += ["--dry-multiplier", str(DRY_MULTIPLIER)]
        if MIROSTAT > 0:
            cmd += ["--mirostat", str(MIROSTAT)]
            cmd += ["--mirostat-lr", str(MIROSTAT_LR)]
            cmd += ["--mirostat-ent", str(MIROSTAT_ENT)]
        if RNG_SEED > 0:
            cmd += ["-s", str(RNG_SEED)]
        if REASONING_FORMAT != "auto":
            cmd += ["--reasoning-format", REASONING_FORMAT]
        if not REASONING_PRESERVE:
            cmd += ["--no-reasoning-preserve"]
        if REASONING_EFFORT not in ("", "default"):
            cmd += ["--reasoning-effort", REASONING_EFFORT]
        if REASONING_BUDGET != -1:
            cmd += ["--reasoning-budget", str(REASONING_BUDGET)]
        if SPEC_TYPE not in ("", "none"):
            cmd += ["--spec-type", SPEC_TYPE]
            cmd += ["--spec-draft-n-max", str(SPEC_DRAFT_N_MAX)]
            cmd += ["--spec-draft-n-min", str(SPEC_DRAFT_N_MIN)]
            cmd += ["--spec-draft-p-split", str(SPEC_DRAFT_P_SPLIT)]
        if JSON_SCHEMA:
            cmd += ["-j", JSON_SCHEMA]
        if GRAMMAR_FILE:
            cmd += ["--grammar-file", GRAMMAR_FILE]
        if IMAGE_FILES:
            for img in IMAGE_FILES:
                cmd += ["--image", img]
        if AUDIO_FILES:
            for aud in AUDIO_FILES:
                cmd += ["--audio", aud]
        if VIDEO_FILES:
            for vid in VIDEO_FILES:
                cmd += ["--video", vid]
        cmd += ["--log-disable"]
        return cmd, prompt_file

    def ask(self, request: str) -> str:
        cmd, prompt_file = self._build_cmd(request)
        started = time.perf_counter()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        elapsed = time.perf_counter() - started
        prompt_file.unlink(missing_ok=True)

        for ln in proc.stderr.splitlines():
            if ln.strip():
                sys.stderr.write(ln + "\n")

        tok_match = re.search(r"\btok/s:\s*([\d.]+)", proc.stderr)
        if tok_match:
            print(f"elapsed_s={elapsed:.2f} tok/s={tok_match.group(1)}",
                  file=sys.stderr)

        return proc.stdout


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Text prompt for the LLM")
    args = parser.parse_args()
    BrainInstaller().install()
    print(Brain().ask(args.request), flush=True)
