# Model Policy Rationale

The purpose of this release is not to make Nano, Turbo, and V3 look uniform. The experimental record showed that uniformity was itself a source of bad decisions.

## Nano

The strongest directly preserved artifact is the correct single-pass 1-20 Nano WAV. The clean reproducible engine state is `aaafdb6`, where the Nano converter uses F16 for the main T3 weights while `text_emb`, `speech_emb`, and `speech_head` remain F32. The launcher therefore preserves that exact mixed-precision direction.

Count policy:
- 1..20: leave monolithic by default.
- 21..30: split at 17.
- >30: split at 16.

This preserves the known-good 1-20 case instead of unnecessarily chunking it at 17, which the historical final Trident code would have done.

## Turbo

The strongest causal A/B in the investigation was Turbo Q8_0 versus F16. F16 substantially improved the long-list result, while the later F32 interface policy did not remove the residual skip-nineteen defect. The final engine nevertheless keeps the interfaces in F32 because this is a precision-preserving parity choice and did not contradict the stronger Turbo state.

Count policy:
- 1..15: monolithic.
- 16..30: chunks of 12.
- >30: chunks of 11.

These are conservative because the official full-precision Python oracle still outperformed the C++ Turbo path on the n=20 acceptance case.

## V3

V3 is treated as a separate architecture. The best recorded V3 counting result occurred in the earlier Q8_0-era state and reached 1..30 correctly. A later F16 V3 limit-discovery state had a lower measured roof. That does not prove Q8_0 is globally better, but it does mean the all-family release should not force V3 onto the later F16 policy without evidence.

Count policy:
- 1..30: monolithic.
- >30: chunks of 22.

V3 keeps its own language parameter and its own header defaults. No Nano/Turbo sampler profile is imposed on it.

## What this release deliberately does not do

- It does not merge the three pipelines.
- It does not force identical sampler settings across architectures.
- It does not promote the historical softmax-attention experiment; that experiment matched the Turbo failure and was slower.
- It does not change the proven product attention path away from the final `ggml_flash_attn_ext + ggml_cont(Q)` GPT-2 implementation in engine `aaafdb6`.
- It does not treat the number ladder as a universal quality metric.
- It does not include a benchmark harness or matrix runner.
