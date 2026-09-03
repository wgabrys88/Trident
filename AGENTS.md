# Trident — TTS-only Runtime (Windows, Intel Iris Xe Vulkan)

## What this repo does

Trident synthesizes text-to-speech using the Chatterbox-Nano ggml port.
It downloads models, builds a native C++ Vulkan server, and runs TTS in a
sequential piece-by-piece pipeline. Current RTF on Iris Xe: ~1.3.
Previous best (commit 0807ba8): 0.797 with 5 long chunks.

## Commands

```powershell
# First-time setup
python main.py install

# Run TTS
python main.py tts --text "Hello world."
python tts.py --text-file input.txt

# Or run each as standalone
python main.py install --models-dir ./models --data-dir ./data
python main.py tts --text "One. Two. Three." --console
```

## Hardware & backend

- **Platform**: Windows only (Python 3.11+, `sys.platform.startswith("win")` enforced)
- **GPU**: Intel Iris Xe detected as `irisxe` hardware class via `detect_hardware()`.
  Uses Vulkan backend (`TTS_BACKEND = "vulkan"`).
- **Known hardware classes**: `pascal` (NVIDIA 6.x/7.x), `irisxe` (Intel Iris Xe).
- **Iris Xe**: Xe1 architecture, SIMD8 cooperative matmul. Xe2 (newer laptops) uses SIMD16
  and is ~2× faster per clock cycle — this is a hardware ceiling.
- **Vulkan env vars**: `VULKAN_ENV` in `config.py`. Pascal sets `GGML_VULKAN_DISABLE_F16=1`.
  Iris Xe currently uses default Vulkan settings.

## Pinned dependencies

| Dependency | Rev | Source |
|---|---|---|
| chatterbox.cpp | `145bef10` | github.com/wgabrys88/chatterbox.cpp |
| ggml | `58c3805` | github.com/ggml-org/ggml |
| chatterbox-nano | `71ccd1d` | HuggingFace ResembleAI/chatterbox-nano |
| Voice (Trump) | `57746b86...` | HuggingFace sdialog/voices-celebrities |
| Python | 3.11+ | `.venv/Scripts/python.exe` |

## File structure

```
trident/
├── main.py           # Entry point: install + tts commands
├── tts.py            # Python-side TTS client: socket I/O, WAV assembly
├── runtime.py        # Residents (chatterbox-server process), Chatterbox client
├── config.py         # Hardware detection, TTS knobs, model specs, Paths
├── install.py        # Download, build, convert models, provenance tracking
├── journal.py        # JSONL event logging, WorkerSupervisor, cleanup
├── requirements.txt  # Empty (pure stdlib + venv)
├── .gitignore        # Whitelist: py files + utils.py (no utils.py at HEAD)
├── tools/
│   ├── runtime/tts/chatterbox-server.exe   # Built C++ server (not in git)
│   └── convert/                             # Python venv for GGUF conversion
├── third_party/
│   └── chatterbox.cpp/                     # Cloned & pinned ggml fork
│       ├── src/server.cpp                  # Native TTS server (TCP, protocol v2)
│       ├── src/chatterbox_engine.cpp       # T3 + S3Gen pipeline
│       ├── src/chatterbox_tts.cpp          # S3Gen synthesis (CPU-only today)
│       └── scripts/                        # Conversion scripts (Python)
└── models/
    ├── chatterbox-t3-nano-q4_0.gguf        # T3 model (Vulkan, GPU-accelerated)
    └── chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf  # S3Gen (CPU today)
```

## Protocol

Native server uses TCP binary protocol (v2):
- Request header: 7 × uint32: MAGIC(0x32525454), VERSION, KIND, EPOCH, RESPONSE, PIECE, LEN
- Response header: 8 × uint32 + payload
- `REQ_SYNTH=1`, `REQ_CLOSE=3`; `RESP_PCM=1`, `RESP_DONE=2`, `RESP_ERROR=4`
- Server port: 17933

## TTS knobs (passed to chatterbox-server)

```python
gpu_layers: 99    # All T3 layers on GPU
context: 2048     # T3 context window
threads: 4        # CPU threads for prep/Vulkan coordination
fastconv: 1       # Fast quantized path
seed: 42
max_tokens: 1000  # Max tokens per piece
top_k: 1000
top_p: 0.95
min_p: 0.05
temperature: 0.8
repeat_penalty: 1.2
cfm_steps: 2      # CFM steps (min for meanflow mode)
cfg_weight: 0.5
exaggeration: 0.5
```

S3Gen runs in **meanflow mode** (confirmed `m.meanflow` true for `turbo` variant).
`cfm_steps=2` is the minimum allowed. Non-meanflow requires `cfm_steps>=5`.

## RTF (Real-Time Factor) baseline

- **Current (30 short sentences)**: RTF = 1.31 (28.06s wall / 21.42s audio)
- **Previous best (commit 0807ba8)**: RTF = 0.797 (5 long chunks, ~105 chars each)
- **Current with greedy chunking (`_TEXT_CHUNK_CHARS=75`)**: RTF ≈ 0.85 (4 chunks, ~70 chars each)
- **Target**: RTF < 0.5

RTF is logged as `tts.completed rtf=N` in `events.jsonl`.
Formula: `rtf = elapsed_ms / (duration_s * 1000)` — lower is faster.

**Key insight**: Per-piece S3Gen overhead is ~500ms (encoder + CFM + F0 + STFT + HiFT on
GPU). For 30 short pieces that overhead dominates; merging short sentences into longer
chunks (greedy pack at `_TEXT_CHUNK_CHARS` chars) halves the RTF. T3 forward pass cost
is ~50ms per token, so chunks of 70-80 chars balance T3 time vs S3Gen overhead.

The C++ S3Gen encoder rebuilds its compute graph when token count T changes, so pieces
with similar token counts amortize the rebuild cost — this favors mid-length chunks
where consecutive pieces produce similar token counts.

## Performance notes (Iris Xe Vulkan)

1. **T3 is GPU-accelerated, S3Gen is also on Vulkan GPU** (Iris Xe1 SIMD8).
   `s3gen_init_backend()` in `chatterbox_tts.cpp:100` calls `ggml_backend_vk_init(0)`
   for any `n_gpu_layers > 0` (default 99). Both models run on the same Iris Xe GPU.

2. **Per-piece overhead is the dominant cost**. Each `piece_streaming()` call in
   `chatterbox_engine.cpp:135` runs the full T3 forward + S3Gen encoder + CFM + F0 +
   STFT + HiFT. S3Gen alone is ~500ms/piece on Iris Xe. Merging short sentences
   into longer chunks (greedy pack at `_TEXT_CHUNK_CHARS=75`) cuts RTF from
   1.31 to ~0.85.

3. **Threads=4 is conservative**. Iris Xe has 4-8 CPU cores. Increasing to 8
   improves Vulkan queue coordination and S3Gen prep. Try `--threads 8`.

4. **GGML Vulkan env vars for Iris Xe**:
   - `GGML_VULKAN_DISABLE_F16=0` (Iris Xe supports FP16 unlike Pascal)
   - `GGML_VULKAN_VULKAN_DEVICE=0` (explicit device selection)
   - `GGML_VULKAN_SHADER_CACHE_DIR` (avoid recompilation per run)
   - `GGML_VULKAN_ENABLE_DEBUG=0` (disable debug logging)

5. **Intel Xe1 vs Xe2 detection** in `ggml-vulkan.cpp`:
   - Xe2: `minSubgroupSize == 16` (SIMD16)
   - Xe1 (Iris Xe): `minSubgroupSize == 8` with integer dot product (SIMD8)
   - This is a hardware ceiling — no SW fix will bridge the gap on Xe1.

6. **Batched S3Gen encoder across pieces** was attempted in `chatterbox_engine.cpp`
   but **crashes on Iris Xe** with `ggml_vulkan: Error: Missing multi_add` — Vulkan
   shader resource limit. The 30-piece batched run creates a too-large compute graph.
   Server-side batching of pending pieces is the safe alternative.

## Code conventions

- All Python files call `ensure_venv(__file__)` at top.
- `main.py` dispatches to `install.py` or `tts.py` via `__import__(cmd).launch()`.
- Provenance is enforced: if built artifacts don't match pinned commits, `python main.py install` is required.
- Protocol constants (`PROTOCOL_MAGIC`, etc.) are defined in `runtime.py` and imported by `tts.py`.
- Socket I/O is encapsulated in `WireProtocol` class (`runtime.py`); `Chatterbox`
  owns the connection and exposes `open`/`request_close`/`disconnect`.
- TTS command-line args are built by `TTSProfile.cmd_args()` (dataclass in `runtime.py`).
- Thread supervision is done directly with `threading.Event.wait` + deadline polling
  in `Residents._start_resident`; the old `WorkerSupervisor` was removed (dead code).

## Rebuild triggers

Rebuild chatterbox-server.exe when:
- `CHATTERBOX_REV` or `GGML_GIT` changes in `config.py`
- `HARDWARE` changes (different GPU)
- `TTS_BACKEND` changes

Run `python main.py install` after any rebuild to regenerate provenance.

## Working with chatterbox.cpp

Trident pins a specific commit of the upstream chatterbox.cpp fork. When modifying:
1. Make changes in `third_party/chatterbox.cpp/`
2. Test: `cmake --build tools/tts-build --config Release --target chatterbox-server --parallel`
3. Update `CHATTERBOX_REV` in `config.py` to the new commit SHA
4. Update extended commit message with: what changed, why, expected RTF impact
5. Run `python main.py install` to update provenance

## Known issues

- `utils.py` was removed (last present at commit c78f737). Don't recreate it —
  `journal.py` no longer references it after `Journal.resample` was removed.
- `requirements.txt` is intentionally empty. All deps come from `.venv`.
- The `paths.supervisor` attribute was removed from `Paths`; if you add new code
  that needs supervised thread management, use `threading.Event.wait` with a
  deadline like `Residents._start_resident` does.
