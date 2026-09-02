import argparse
import math
import sys
from pathlib import Path

from config import (
    ASR_LOCALES, ASR_RATE, CHATTERBOX, CHATTERBOX_REV, FLASH_ATTN, GEMMA_GEN, GEMMA_RUNTIME, GGML, GGML_GIT,
    HARDWARE, PARAKEET_FILE, Paths, ROOT, TTS_MODELS, TTS_PROFILES, TTS_RATE, VULKAN_ENV, ensure_venv,
    load_settings, wasapi_device,
)
import config
from install import install, product_stamps
from journal import git_identity

ensure_venv(__file__)


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file(): parser.error(f"{label} does not exist: {path}")
    try: return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError: parser.error(f"{label} is not valid UTF-8")


def _manifest(paths: Paths) -> dict:
    settings, audio = load_settings(paths.data_dir), {}
    for kind in {"talk": ("input", "output"), "tts": ("output",), "asr": ("input",)}.get(paths.command, ()):
        index, device, host = wasapi_device(kind)
        audio[kind] = {"device": device["name"], "index": index, "host_api": host["name"],
                       "channels": 1, "rate": ASR_RATE if kind == "input" else TTS_RATE, "auto_convert": True}
    runtime_knobs = {}
    if hasattr(paths, "tts_knobs"): runtime_knobs["tts"] = paths.tts_knobs
    if hasattr(paths, "gemma_runtime"):
        runtime_knobs["gemma"] = {"server": paths.gemma_runtime, "generation": paths.gemma_gen,
                                  "thinking": paths.thinking, "thinking_budget": paths.thinking_budget,
                                  "tools": paths.tools_enabled}
    if paths.command in ("talk", "asr"):
        runtime_knobs["asr"] = {"mode": "capi-stream", "model": PARAKEET_FILE,
                                "device": paths.asr_device or "auto", "locale": ASR_LOCALES[paths.language],
                                "prefill_min_words": getattr(paths, "prefill_min_words", None)}
    manifest = {
        "created_at": paths.stamp, "command": paths.command, "family": paths.family,
        "language": paths.language, "voice": paths.voice,
        "repositories": {
            "trident": git_identity(ROOT),
            "chatterbox": {**git_identity(CHATTERBOX), "configured_pin": CHATTERBOX_REV},
            "ggml": {**git_identity(GGML), "configured_pin": GGML_GIT[1]},
        },
        "interpreter": {"path": sys.executable, "version": sys.version},
        "machine": {"hardware": HARDWARE, "gpu": config.GPU_NAME, "backend": config.TTS_BACKEND,
                    "codec": TTS_MODELS.get(paths.family, (None, None))[1], "vulkan_env": VULKAN_ENV,
                    "flash_attn": getattr(paths, "gemma_runtime", {}).get("flash_attn", FLASH_ATTN)},
        "runtime_knobs": runtime_knobs,
        "conversation": {**{k: settings.get(k) for k in ("candidate_silence_ms", "completion_threshold", "acoustic_context_seconds")},
                         "history_mode": getattr(paths, "history_mode", settings.get("history_mode", "conversation")),
                         "history_turns": getattr(paths, "history_turns", settings.get("history_turns", 16))},
        "audio": audio,
    }
    if paths.command != "install": manifest["products"] = product_stamps(paths.models_dir)
    return manifest


def main(command: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python {command}.py" if command else "python main.py")
    parser.add_argument("--models-dir", type=Path); parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano"); parser.add_argument("--language", default="en")
    parser.add_argument("--voice", help="installed voice name or WAV path for talk/tts")
    parser.add_argument("--console", action="store_true")

    # Native TTS performance controls.
    parser.add_argument("--tts-threads", type=int, help="override native TTS host thread count")
    parser.add_argument("--cfm-steps", type=int, help="override S3Gen CFM/MeanFlow steps")
    parser.add_argument("--fastconv", type=int, choices=(0, 1), help="override S3Gen F16-convolution baking")
    parser.add_argument("--cfg-weight", type=float, help="V3 classifier-free guidance; upstream default 0.5")
    parser.add_argument("--exaggeration", type=float, help="V3 reference emotion strength; upstream default 0.5")

    # Gemma capability/performance controls.
    parser.add_argument("--gemma-device", help="llama.cpp device, default Vulkan0")
    parser.add_argument("--gemma-gpu-layers", help="llama.cpp GPU layer offload count or 'all'")
    parser.add_argument("--gemma-context", type=int, help="llama-server context size (model supports up to 131072 on E2B)")
    parser.add_argument("--gemma-parallel", type=int, help="llama-server slot count; voice default is one")
    parser.add_argument("--gemma-threads", type=int); parser.add_argument("--gemma-threads-batch", type=int)
    parser.add_argument("--gemma-max-tokens", type=int); parser.add_argument("--gemma-temperature", type=float)
    parser.add_argument("--gemma-top-p", type=float); parser.add_argument("--gemma-top-k", type=int)
    parser.add_argument("--gemma-min-p", type=float); parser.add_argument("--gemma-repeat-penalty", type=float)
    parser.add_argument("--gemma-seed", type=int)
    parser.add_argument("--gemma-cache-type-k", choices=("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"))
    parser.add_argument("--gemma-cache-type-v", choices=("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"))
    parser.add_argument("--gemma-cache-ram", type=int, help="llama-server prompt-cache RAM limit in MiB")
    parser.add_argument("--gemma-ctx-checkpoints", type=int); parser.add_argument("--gemma-checkpoint-min-step", type=int)
    parser.add_argument("--gemma-batch-size", type=int); parser.add_argument("--gemma-ubatch-size", type=int)
    parser.add_argument("--flash-attn", choices=("auto", "on", "off"))
    parser.add_argument("--thinking", choices=("auto", "on", "off")); parser.add_argument("--thinking-budget", type=int)
    parser.add_argument("--history", choices=("conversation", "turn")); parser.add_argument("--history-turns", type=int)
    parser.add_argument("--tools", action=argparse.BooleanOptionalAction, default=None, help="allow the built-in hello_world tool")
    parser.add_argument("--system-prompt"); parser.add_argument("--system-prompt-file", type=Path)

    # True cache-aware streaming ASR; partial text always feeds zero-token Gemma prefill in talk.
    parser.add_argument("--asr-device", help="parakeet.cpp PARAKEET_DEVICE override, e.g. cpu or Vulkan0")
    parser.add_argument("--prefill-min-words", type=int, help="minimum finalized streaming-ASR words before Gemma prompt prefill")

    parser.add_argument("--text"); parser.add_argument("--text-file", type=Path)
    parser.add_argument("--interrupt-text"); parser.add_argument("--interrupt-file", type=Path); parser.add_argument("--interrupt-after", type=float)
    if command is None:
        parser.add_argument("command", nargs="?", choices=("install", "talk", "tts", "asr", "generation"), default="install")
    args = parser.parse_args(); cmd = command or args.command

    primary = replacement = None
    content_flags = (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)
    if cmd in ("tts", "generation"):
        if cmd == "generation" and any(v is not None for v in content_flags[2:]): parser.error("generation does not accept interrupt flags")
        if (args.text is None) == (args.text_file is None): parser.error("exactly one of --text and --text-file is required")
        if cmd == "tts":
            if args.interrupt_text is not None and args.interrupt_file is not None: parser.error("--interrupt-text and --interrupt-file are mutually exclusive")
            if (args.interrupt_text is not None or args.interrupt_file is not None) != (args.interrupt_after is not None): parser.error("interrupt content and --interrupt-after are required together")
            if args.interrupt_after is not None and (not math.isfinite(args.interrupt_after) or args.interrupt_after < 0): parser.error("--interrupt-after must be finite and non-negative")
        primary = (args.text if args.text is not None else _read_utf8(args.text_file, parser, "--text-file")).strip()
        replacement = args.interrupt_text if args.interrupt_text is not None else (_read_utf8(args.interrupt_file, parser, "--interrupt-file") if args.interrupt_file is not None else None)
        replacement = replacement.strip() if replacement is not None else None
        if not primary: parser.error("TTS input is empty" if cmd == "tts" else "generation input is empty")
        if replacement is not None and not replacement: parser.error("TTS replacement is empty")
    elif any(v is not None for v in content_flags):
        parser.error("TTS text and replacement flags require command tts" if cmd == "talk" else f"{cmd} does not accept streaming or TTS content flags")

    family, language = (args.family, args.language.strip().lower()) if cmd != "install" else ("all", "all")
    if HARDWARE == "irisxe" and cmd in ("talk", "tts") and family != "nano": parser.error("Iris Xe supports Nano English only")

    tts_options = (args.tts_threads, args.cfm_steps, args.fastconv, args.cfg_weight, args.exaggeration, args.voice)
    if cmd not in ("talk", "tts") and any(v is not None for v in tts_options): parser.error("TTS performance overrides require command talk or tts")
    if args.tts_threads is not None and args.tts_threads < 1: parser.error("--tts-threads must be positive")
    if args.cfm_steps is not None and args.cfm_steps < (5 if family == "v3" else 1): parser.error("--cfm-steps must be at least 5 for V3 and at least 1 otherwise")
    if args.cfg_weight is not None and args.cfg_weight < 0: parser.error("--cfg-weight must be non-negative")
    if args.exaggeration is not None and args.exaggeration < 0: parser.error("--exaggeration must be non-negative")
    if args.voice is not None and not args.voice.strip(): parser.error("--voice must not be empty")

    gemma_options = (args.gemma_device, args.gemma_context, args.gemma_parallel, args.gemma_threads, args.gemma_threads_batch,
                     args.gemma_max_tokens, args.gemma_temperature, args.gemma_top_p, args.gemma_top_k,
                     args.gemma_min_p, args.gemma_repeat_penalty, args.gemma_seed, args.gemma_cache_ram, args.gemma_ctx_checkpoints,
                     args.gemma_checkpoint_min_step, args.gemma_batch_size, args.gemma_ubatch_size, args.flash_attn, args.thinking,
                     args.thinking_budget, args.history, args.history_turns, args.tools, args.system_prompt, args.system_prompt_file)
    if cmd not in ("talk", "generation") and any(v is not None for v in gemma_options): parser.error("Gemma options require command talk or generation")
    if args.system_prompt is not None and args.system_prompt_file is not None: parser.error("--system-prompt and --system-prompt-file are mutually exclusive")
    if args.gemma_device is not None and not args.gemma_device.strip(): parser.error("--gemma-device must not be empty")
    if args.asr_device is not None and not args.asr_device.strip(): parser.error("--asr-device must not be empty")
    if args.gemma_context is not None and not 2048 <= args.gemma_context <= 131072: parser.error("--gemma-context must be between 2048 and 131072")
    if args.gemma_max_tokens is not None and args.gemma_max_tokens < 1: parser.error("--gemma-max-tokens must be positive")
    for value, label in ((args.gemma_top_p, "--gemma-top-p"), (args.gemma_min_p, "--gemma-min-p")):
        if value is not None and not 0 <= value <= 1: parser.error(f"{label} must be between 0 and 1")
    if args.gemma_temperature is not None and args.gemma_temperature < 0: parser.error("--gemma-temperature must be non-negative")
    if args.gemma_top_k is not None and args.gemma_top_k < 0: parser.error("--gemma-top-k must be non-negative")
    if args.gemma_repeat_penalty is not None and args.gemma_repeat_penalty <= 0: parser.error("--gemma-repeat-penalty must be positive")
    if args.thinking_budget is not None and args.thinking_budget < -1: parser.error("--thinking-budget must be -1 or non-negative")
    if args.history_turns is not None and args.history_turns < 1: parser.error("--history-turns must be positive")
    for value, label in ((args.gemma_cache_ram, "--gemma-cache-ram"), (args.gemma_ctx_checkpoints, "--gemma-ctx-checkpoints"),
                         (args.gemma_checkpoint_min_step, "--gemma-checkpoint-min-step")):
        if value is not None and value < 0: parser.error(f"{label} must be non-negative")
    for value, label in ((args.gemma_parallel, "--gemma-parallel"), (args.gemma_threads, "--gemma-threads"),
                         (args.gemma_threads_batch, "--gemma-threads-batch"), (args.gemma_batch_size, "--gemma-batch-size"),
                         (args.gemma_ubatch_size, "--gemma-ubatch-size")):
        if value is not None and value < 1: parser.error(f"{label} must be positive")

    if cmd not in ("talk", "asr") and args.asr_device is not None:
        parser.error("--asr-device requires command talk or asr")
    if cmd != "talk" and args.prefill_min_words is not None:
        parser.error("--prefill-min-words requires command talk")
    if args.prefill_min_words is not None and args.prefill_min_words < 1: parser.error("--prefill-min-words must be positive")

    if cmd in ("talk", "asr") and language not in ASR_LOCALES:
        parser.error(f"streaming Parakeet has no validated locale for {language!r}; use a supported ASR language")
    paths = Paths(args.models_dir, args.data_dir, cmd, family, language, args.console); settings = load_settings(paths.data_dir)
    if cmd in ("talk", "tts"):
        if args.voice is not None: paths.voice = args.voice.strip()
        paths.tts_knobs = dict(TTS_PROFILES[family])
        for key, value in (("threads", args.tts_threads), ("cfm_steps", args.cfm_steps), ("fastconv", args.fastconv),
                           ("cfg_weight", args.cfg_weight), ("exaggeration", args.exaggeration)):
            if value is not None: paths.tts_knobs[key] = value
    if cmd in ("talk", "generation"):
        paths.gemma_gen = dict(GEMMA_GEN)
        for key, value in (("max_tokens", args.gemma_max_tokens), ("temperature", args.gemma_temperature), ("top_p", args.gemma_top_p),
                           ("top_k", args.gemma_top_k), ("min_p", args.gemma_min_p), ("repeat_penalty", args.gemma_repeat_penalty), ("seed", args.gemma_seed)):
            if value is not None: paths.gemma_gen[key] = value
        paths.gemma_runtime = dict(GEMMA_RUNTIME)
        if cmd == "talk" and args.gemma_context is None:
            paths.gemma_runtime["context"] = 8192
        for key, value in (("device", args.gemma_device), ("gpu_layers", args.gemma_gpu_layers), ("context", args.gemma_context), ("parallel", args.gemma_parallel),
                           ("threads", args.gemma_threads), ("threads_batch", args.gemma_threads_batch),
                           ("cache_type_k", args.gemma_cache_type_k), ("cache_type_v", args.gemma_cache_type_v),
                           ("cache_ram", args.gemma_cache_ram), ("ctx_checkpoints", args.gemma_ctx_checkpoints),
                           ("checkpoint_min_step", args.gemma_checkpoint_min_step), ("batch_size", args.gemma_batch_size), ("ubatch_size", args.gemma_ubatch_size)):
            if value is not None: paths.gemma_runtime[key] = value
        paths.gemma_runtime["flash_attn"] = args.flash_attn or FLASH_ATTN
        paths.thinking = args.thinking or str(settings.get("thinking") or ("off" if cmd == "talk" else "auto"))
        paths.thinking_budget = args.thinking_budget if args.thinking_budget is not None else int(settings.get("thinking_budget", -1))
        paths.history_mode = args.history or str(settings.get("history_mode") or "conversation")
        paths.history_turns = args.history_turns or int(settings.get("history_turns", 16))
        if paths.thinking not in ("auto", "on", "off"): parser.error("live-settings thinking must be auto, on, or off")
        if paths.thinking_budget < -1: parser.error("live-settings thinking_budget must be -1 or non-negative")
        if paths.history_mode not in ("conversation", "turn"): parser.error("live-settings history_mode must be conversation or turn")
        if paths.history_turns < 1: parser.error("live-settings history_turns must be positive")
        paths.tools_enabled = bool(settings.get("tools_enabled", False)) if args.tools is None else args.tools
        paths.system_prompt = (args.system_prompt if args.system_prompt is not None else
                               _read_utf8(args.system_prompt_file, parser, "--system-prompt-file") if args.system_prompt_file is not None else
                               str(settings.get("system_prompt") or config.PROMPT)).strip()
        if not paths.system_prompt: parser.error("system prompt is empty")
    if cmd in ("talk", "asr"):
        paths.asr_device = (args.asr_device if args.asr_device is not None else str(settings.get("asr_device") or "")).strip()
    if cmd == "talk":
        paths.prefill_min_words = args.prefill_min_words or int(settings.get("prefill_min_words", 2))
        if paths.prefill_min_words < 1: parser.error("live-settings prefill_min_words must be positive")

    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=cmd, hardware=HARDWARE, family=family, language=language)
        if cmd == "install": install(args.models_dir, args.data_dir, paths)
        else: __import__(cmd).launch(paths, args.family, language, primary, replacement, args.interrupt_after)
        paths.journal.emit("main", "completed", command=cmd); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
