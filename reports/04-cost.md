# 04. Cost and economics

Date of this reading: Tuesday 27 October 2026. Branch `runner-h`, HEAD `190058900aaafd8e2a8bdb6bbdc9b3dc45671a11`. Workspace `C:\Users\eb-wjt\Downloads\Jarvis\Trident`.

A line-by-line reader consumed every line of the 16 run markdown files (19,714 lines), the 5 run jsonl files that exist (33,166 lines, 0 decode failures), and all 29 files in `streams\` (the four stream paths named by `run_20261027_133306.jsonl` are no longer on disk). `organism.py` (1,044 lines), `main.py` (3 lines), `ledger.md` (39 lines), `prompt.txt` (200 lines), and `skills\.gitkeep` (0 bytes) were read in full. `edit_log.txt` is not in the tree: a recursive search by that filename returned no path, and a workspace search for the same name returned 0 files.

Labels used below: OBSERVED means the number is copied from a file or from a page read on this date. INFERRED means an arithmetic step the file does not itself perform. Confidence is stated on inferences. A price that was not printed on a page is not used.

## 1. Scope, method, and price sources

### What was measured

The mind is a Cursor agent process. `organism.py` line 832 builds the command with `--output-format stream-json` and `--model` set to the slug from `brain()` (lines 791–795). The default slug is `grok-4.7-medium-fast`. The eye is one HTTP call in `ask()`:

```511:512:organism.py
    payload = {'model': SET['vision_model'], 'temperature': 0, 'max_tokens': 2048, 'chat_template_kwargs': {'enable_thinking': False}, 'messages': messages}
    request = urllib.request.Request(SET['vision_url'] + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
```

`main.py` lines 1–3 only load `organism.py`. It has no price and no token counter.

Two clocks are kept apart:

- Money is a published per-token rate applied only where a usage object exists.
- Time is `duration_ms` on a mind `result` event, `prompt_ms` plus `predicted_ms` on an eye response, the jsonl `at` span of a chat, or the `HH:MM:SS` span of markdown headings. Heading timestamps resolve to 1 second. Eye timings in the jsonl resolve to microseconds. No PNG was opened: the eye timings and the heading gaps already carry the time, and the pixels are not a second clock.

### Where token totals exist, and where they do not

Per model call, no usage object exists. Across every jsonl line and every stream line, `usage` sits on `eye_response`, on the parsed `component` copy of an eye response, on `wake_end` (a copy of those records), and on `type=result`. `model_call_id` values do not carry tokens. `thinking/completed` does not carry tokens.

Per chat, a mind total exists only when the agent printed `type=result` before the wake killed it. That happened for 8 chats. The other chats that reached a model `init` (22 of them) have a wall clock and a tool count and no token total. Three further chats died before `init` (empty or non-JSON stream files of 187, 183, and 21 bytes).

Per wake, the program's own rollup refuses to sum. `run_20261027_133306.jsonl` line 9960, kind `wake_end`, at 14:06:04.555:

> `"tokens": null, "estimated_usd": null, "money": "not metered", "aggregation": "Raw per-call usage retained; mixed/possibly cumulative agent schemas are not summed."`

The same sentence is on `run_20261027_141013.jsonl` line 5546, `run_20261027_143208.jsonl` line 6045, and `run_20261027_151228.jsonl` line 11034. The writer of that sentence is the removed file `evidence.py` at commit `8e3e4837` (AuthorDate Tue Oct 27 10:25:29 2026 +0100), lines 100–114 and 135–136:

> `out = dict(usage=usage, estimated_usd=None, money='not metered')`
>
> `if rates.get('usage_schema') != 'input_includes_cached' or not all(key in usage and key in rates for key in required): return out`
>
> `aggregation='Raw per-call usage retained; mixed/possibly cumulative agent schemas are not summed.'`

`required` is `input_tokens`, `cached_input_tokens`, `output_tokens`. Eye records use `prompt_tokens` and `completion_tokens`. Mind records use `inputTokens`, `outputTokens`, `cacheReadTokens`. Neither schema matches, so `estimated_usd` stays null even when tokens are present. Commit `9823582` (AuthorDate Tue Oct 27 15:42:51 2026 +0100, "Collapse the body into one organism.py under 1000 lines") is the last commit `git log -S estimated_usd` names. After it, no run jsonl exists.

The result objects that do exist are chat-level sums, not one call. Cursor's model page, read 27 October 2026, caps the window at 500k tokens (quote below). These cache counts are above that cap, so they cannot be one request:

| Chat | `cacheReadTokens` | `inputTokens` + `cacheReadTokens` |
| --- | ---: | ---: |
| 183727 | 530048 | 576029 |
| 160354 last chat | 662016 | 776172 |
| 202606 | 881024 | 1010613 |
| 200412 | 914432 | 992611 |
| 191902 | 955008 | 1085629 |
| 201442 | 2132224 | 2239535 |

OBSERVED. Confidence high that these six are not single requests. INFERRED, confidence medium: the CLI summed per-call usage into `result.usage`. The file never says "sum". An even split across `model_call_id` counts is not in the file and is not used as a measured per-call total. The two earlier result objects (133306 chat 4, sum of input and cache 365675; 143208 chat 4, sum 499480) fit inside a 500k window, so they can be either one long request or a sum. That ambiguity is left open.

`cacheWriteTokens` is 0 on every result object.

### Price sources, read 27 October 2026

No invoice, dashboard balance, or pool remainder is in the repo. Every dollar figure below is a list-price equivalent for additional usage, not cash observed leaving an account. The Cursor page says the weekly pool can absorb Cursor-model usage, and this review cannot see whether the pool was exhausted.

**Cursor Grok 4.7, the model the process launches.**

- URL: https://cursor.com/docs/models/grok-4-7
- Read: 27 October 2026
- Verbatim: "Additional usage beyond the pool at standard speed is priced at $2/M input tokens, $0.50/M cached input tokens, and $6/M output tokens. Fast is $4/M input, $1/M cached, and $12/M output. When input exceeds 256k tokens, standard requests bill at 2x those rates and Fast requests bill at 3x the standard rates, up to a 500k context window. Long-context billing uses the input length only: if input is at or below 256k tokens, the full request uses the standard or Fast rates."
- Same page, effort: "Higher effort gives the model more time to work through difficult tasks." The page states no separate token price for `low`, `medium`, `high`, or `xhigh`.
- Same page, window names: "a 256k standard window and 500k long context."

- URL: https://cursor.com/docs/models-and-pricing
- Read: 27 October 2026 (saved extract, 22,060 bytes, 215 lines)
- Table row, verbatim cells: `Grok 4.7 (Fast)` provider Cursor, Input `$4`, Cache write `-`, Cache read `$1`, Output `$12`. The row note also says "Long context (>256k input tokens) is billed at 2x standard rates", which conflicts with the model-page sentence that Fast long context is 3x the standard rates. The model-page sentence is the one used here when a long-context alternative is shown. The table row `Grok 4.7 500k (Fast)` is Input `$6`, Cache read `$1.5`, Output `$18`, which matches 3x the standard $2 / $0.50 / $6.
- Same page: "These rates apply when you buy additional usage beyond the weekly pool."
- Same page: "The Cursor Token Rate does not apply to Cursor models, including Grok and Composer, or to Cost mode requests." No $0.25/M adder is applied.
- Same page: "Opting in to regional data residency incurs a 10% uplift on Model pricing." This review has no record of that opt-in, so the 10% is not applied.

The agent `init` strings are `Grok 4.7 256K Extra High Fast` and `Grok 4.7 256K Medium Fast`. Both contain `256K` and `Fast`. The matching published row used here is Grok 4.7 (Fast) at $4 / $1 / $12 per million, unless a single request's input length is shown to exceed 256k. Effort (`Extra High` versus `Medium`) has no published multiplier.

**xAI / SpaceXSI API, read because the Cursor page says the model is jointly trained and because the fast variant is documented there as a Cursor price.**

- URL: https://docs.x.ai/developers/pricing
- Read: 27 October 2026
- Table, verbatim: `grok-4.7 (< 200k prompt tokens)` context 500k, input `$2.00`, cached `$0.50`, output `$6.00`. `grok-4.7 (≥ 200k prompt tokens)` input `$4.00`, cached `$1.00`, output `$12.00`.
- Same page, "Grok 4.7 Fast pricing (Cursor and Grok Build only)": "It costs 2x the standard token rates, or 1.5x for long-context requests. It's available only in Cursor and Grok Build, and is billed through your plan there. It is not available on the public xAI API." Table: below 200k, `$4.00 / 1M`, `$1.00 / 1M`, `$12.00 / 1M`. Above 200k, `$6.00 / 1M`, `$1.50 / 1M`, `$18.00 / 1M`.
- The public API threshold is 200k. The Cursor page threshold is 256k. For a prompt between those two numbers the pages disagree. None of the six chat sums that exceed 500k can be priced as one prompt. Their even-split averages, if one were to divide (not done as a measurement), would sit under 80k and then both pages would use $4 / $1 / $12. The US regional 1.1x rate on this page is for `https://us.api.x.ai/v1`. The wake does not call that host. It is not applied.

The process path is the Cursor CLI (`organism.py` line 832, `--model`), and the xAI page says the fast variant "is billed through your plan there." The Cursor Fast row is the rate used for the list-price column. The xAI standard (non-fast) row would be the rate only for a public API call, which these wakes do not make.

**Local eye: Fara and llama.cpp.**

- URL: https://huggingface.co/microsoft/Fara1.5-4B
- Read: 27 October 2026
- Verbatim: "License | MIT" and "Released under the MIT License." The card states no per-token price. Its sample client sets `max_tokens=2048` and says "We only keep the most recent 3 screenshots," which matches `ask()` (`max_tokens` 2048, `kept` last 3 shots at line 502). That is a behavior match, not a price.
- URL: https://github.com/ggml-org/llama.cpp/blob/HEAD/LICENSE
- Read: 27 October 2026
- Verbatim: "Permission is hereby granted, free of charge, to any person obtaining a copy of this software." The license prices the software at no charge. It states no per-token inference price. A search of the llama.cpp server README for a price returned no per-token rate (the only hits were the word `billing` inside an example schema).
- In the runs, the meter says the eye is not metered. `run_20261027_133306.jsonl` line 4518, kind `component`: `"money": "not metered"`, `"estimated_usd": null`, `"reported_model": "fara-1.5-4b"`, `"requested_model": "retina"`, usage identical to the eye response on line 4517. All 12 priced components in that file and all 52 priced components in `run_20261027_143208.jsonl` carry the same `not metered` / `null` pair. Electricity in watt-hours was not measured. No kWh figure is invented.

### List-price formula used when a mind `result` exists

Fast, input length at or below 256k, additional usage, no residency uplift:

`USD = (inputTokens * 4 + cacheReadTokens * 1 + outputTokens * 12) / 1_000_000`

`inputTokens` and `cacheReadTokens` are added because, on every result, `cacheReadTokens` is larger than `inputTokens`, so the cache count is not a subset of `inputTokens`. The old `evidence.py` schema required the opposite (`cached <= input`, line 109) and therefore refused these objects. The additive reading is INFERRED, confidence medium.

If one of the two ambiguous results is a single request over 256k, Fast long context on the Cursor model page is 3x standard: $6 / $1.50 / $18 per million. That alternative is shown only for those two chats.

The eight computed equivalents sum to **$12.084300**. That is a lower bound on list-price additional usage for the chats that emitted a result. It is not a bill, and it omits the 22 chats with no usage object.

### Code facts that set the cost, independent of the runs

- Rotation at 42 completed tool calls: `organism.py` lines 902–903, `if calls >= 42 and running == 0 ...: return finish('rotate')`. `finish` (lines 854–857) kills the process if it is still running. A killed process does not print `result`, so the usage line never arrives.
- Deadline does the same immediately: lines 860–861, `return finish('deadline')`.
- A stop file waits up to 60 seconds (lines 862–865) and can still receive `result`. Every chat that has a usage object ended on a stop or a natural result, not on `rotate`.
- The eye thread, lines 939–940, starts `eye_watch` for the whole wake. `--eye-file` while `eye.live` exists returns the log at once (lines 678–685) instead of blocking. Commit `657392cd` (AuthorDate Tue Oct 27 19:41:33 2026 +0100) is the commit that describes this split. Before that commit, an eye call blocked the mind.
- `shot_delay` default `1.5` seconds is slept after a successful action (line 654). Mouse moves also sleep 0.03 seconds (line 290). Those sleeps are time. They are not tokens.
- `decide_model` is still passed as `typesafe/jev-1.14-20261014` (`prompt.txt` line 185; every `wake_start` in the jsonl). `TOOLS` at lines 986–989 has no decide tool. The 33,166 jsonl lines contain no `decision_response`. Markdown heading kinds contain no `decide`. Jev's observed call count is 0.

## 2. Timeline, one subsection per run

Ledger "used" minutes are the second clock for the wake. Heading span is `last HH:MM:SS` minus `first HH:MM:SS` in that run's markdown. Jsonl chat wall is the last `at` minus the first `at` inside that chat.

Mind list price, when shown, uses the formula in section 1. Eye money is the file's own `not metered`, not a converted dollar.

### 2.1 `20261027_133306` — 13:33:06, 40 minutes

Model, two records in `run_20261027_133306.jsonl`: line 2 `chat_prompt` at 13:33:06.523, `"model": "grok-4.7-xhigh-fast"`; line 4 agent `init`, `"model": "Grok 4.7 256K Extra High Fast"`. The markdown line 3 repeats `Grok 4.7 256K Extra High Fast`. All four chats use that pair. Vision: `wake_start` line 1, `"vision_model": "retina"`, `"vision_url": "http://192.168.16.31:1234"`.

Wake wall: jsonl `wake_start` 13:33:06.275 to `wake_end` 14:06:04.555 is 1,978.28 seconds. Markdown headings 13:33:12 to 14:06:04, span 1,972 seconds. Ledger line 11: "used 33.0 of 40 min, stopped". 33.0 minutes is 1,980 seconds.

Mind chats (jsonl):

| Chat | Tools done | `model_call_id` count | Wall | Exit | Mind tokens |
| --- | ---: | ---: | ---: | --- | --- |
| :1 | 42 | 13 | 599.883 s (13:33:12.029–13:43:11.912) | `rotate`, return code 1, line 3445 | none |
| :2 | 43 | 32 | 699.884 s | `rotate`, line 6167 | none |
| :3 | 42 | 13 | 332.693 s | `rotate`, line 8138 | none |
| :4 | 18 | 7 | 327.668 s | `stopped`, return code 0, line 9959 | present |

Chat :4 result, jsonl line 9957, at 14:06:04.060:

> `"duration_ms": 327635, "duration_api_ms": 327635`
>
> `"usage": {"inputTokens": 73067, "outputTokens": 30941, "cacheReadTokens": 292608, "cacheWriteTokens": 0}`

INFERRED Fast short-context list price: **$0.956168**. If this object is one request, input+cache is 365,675 tokens, which is over 256k, and the Fast long-context alternative is **$1.434252**. The file does not say which. Prompt characters, jsonl `chat_prompt` text lengths: chat 1 = 21,088; chat 2 = 17,340; chat 3 = 17,317; chat 4 = 17,341. Those are characters, not tokens.

The four stream paths on jsonl lines 3, 3447, 6169, and 8140 are absent. Chat :4's result is in the jsonl, so the missing file is not the only copy of that usage. Chats :1–:3 have no result in the jsonl either.

Eye: 15 `eye_response` lines, model `fara-1.5-4b` on every one, requested model `retina`. Markdown `eye` headings: 12. The journal is 3 short of the jsonl. Token sums use the 15 responses.

| | prompt | completion | total | cached |
| --- | ---: | ---: | ---: | ---: |
| sum | 27,939 | 1,459 | 29,398 | 9,138 |
| min / median / max | 1,639 / 1,918 / 1,927 | 50 / 68 / 416 | 1,689 / 1,981 / 2,341 | 0 / 508 / 1,917 |

`prompt_ms` sum 61,930.990, median 4,818.189, max 5,582.522. `predicted_ms` sum 47,205.634, median 2,188.072, max 13,494.237. Model time sum **109.137 seconds**. Wall from request `at` to response `at`: sum 111.230 seconds, median 7.006 seconds. `predicted_per_second` median 30.621. No completion hit 2,048. Money on the component at line 4518: `not metered`, `estimated_usd` null.

Slowest eye call, line 4517, at 13:47:18.614: prompt 1,925, completion 416, total 2,341, cached 0, `prompt_ms` 5,582.522, `predicted_ms` 13,494.237, `predicted_per_second` 30.75386922580358. Model time 19.077 seconds. Fastest, line 5760: `prompt_ms` 79.657, `predicted_ms` 1,592.851, total tokens 1,689, cached 1,635.

Mind tool mix in the markdown (heading `request`): read 63, shell 40, edit 22, grep 12, glob 8. Think text stored in full: 217,155 characters across 66 `think` headings. That character count is not a token count.

### 2.2 `20261027_141013` — 14:10:13, 40 minutes

Model: jsonl line 3, `grok-4.7-xhigh-fast`; agent init `"Grok 4.7 256K Extra High Fast"`. Markdown line 3 is the same display name. One real chat. Jsonl line 1 is `shed`, `"removed": ["agent/config/chats", "agent/data/projects", "streams", "shots", "actions"]`, which is why the 133306 stream files are gone.

Chat :1: 42 tools, 15 `model_call_id`s, wall 854.790 s (14:10:18.602–14:24:33.392), exit `rotate` line 5540, return code 1. No `result`. No mind tokens. Prompt 18,383 characters (jsonl line 3), matched by stream `d5cd315a8aec4383957db3b3f2c544bd.stream` `user_chars` 18,383. That stream's `timestamp_ms` span is 851.340 s and its thinking-delta text is 123,455 characters, matching the markdown think bodies (123,455). The jsonl copy of those deltas has empty `text`. List price: not computed.

Chat :2: jsonl line 5544, `failed_agent`, `completed_calls` 0, at 14:24:33.531. Stream `3e8984ce791c4490be2070da33d1170a.stream` is 183 bytes and is not JSON. No tokens.

Eye calls: 0 in the jsonl, 0 `eye` headings. Wake span: markdown 14:10:18–14:24:33 = 855 s. Ledger line 14: "used 14.3 of 40 min, failed_agent". 14.3 minutes is 858 seconds. `wake_end` line 5546: `money` `not metered`, `n_calls` 0.

Tool mix: read 24, grep 6, shell 6, edit 4, glob 2.

### 2.3 `20261027_142947` — 14:29:47, 40 minutes

Model: jsonl line 2, `grok-4.7-xhigh-fast`; init line 4, `Grok 4.7 256K Extra High Fast`. Markdown line 3 matches. One chat, 7 tools, 4 model calls, wall 87.545 s (14:29:51.530–14:31:19.076). No `chat_end`, no `wake_end`, no `result`. The process stopped mid-chat. Prompt 17,733 characters (jsonl line 2 and stream `0b10ead822cc4433946996d2ad935fa5.stream`). Stream `timestamp_ms` span 83.531 s. Thinking characters 11,780 in both the stream and the markdown. No mind tokens. Eye calls: 0. Markdown span 14:29:51–14:31:19 = 88 s. Ledger.md has no `## wake 20261027_142947` line (the file goes from 141013 on line 14 to 143208 on line 16). Tool mix: shell 3, grep 2, glob 1, read 1, plus one `windows` heading.

### 2.4 `20261027_143208` — 14:32:08, 40 minutes

Model on all four chats: `grok-4.7-xhigh-fast` and `Grok 4.7 256K Extra High Fast` (jsonl line 2 and line 4; markdown line 3). Vision `retina` on `wake_start` line 1.

| Chat | Tools | Model calls | Wall | Exit | Mind tokens |
| --- | ---: | ---: | ---: | --- | --- |
| :1 | 42 | 36 | 551.919 s | `rotate` line 1728 | none |
| :2 | 42 | 37 | 674.971 s | `rotate` line 3634 | none |
| :3 | 42 | 39 | 437.956 s | `rotate` line 4965 | none |
| :4 | 13 | 13 | 296.060 s | `stopped` line 6044, return code 0 | present |

Chat :4 result, jsonl line 6042, at 15:05:06.198, and the same object on stream `7e394b3b958646779b2e8a50ddb9a5a4.stream` line 1047:

> `"duration_ms": 296048, "duration_api_ms": 296048`
>
> `"usage": {"inputTokens": 134680, "outputTokens": 21767, "cacheReadTokens": 364800, "cacheWriteTokens": 0}`

INFERRED Fast short-context list price: **$1.164724**. If this is one request, input+cache is 499,480, over 256k, and the long-context alternative is **$1.747086**. Prompt characters: 18,168, 19,895, 19,462, 20,458.

Eye: 66 `eye_response` lines, all `fara-1.5-4b` / requested `retina`. Markdown `eye` headings: 52. Component rows with usage: 52, token sum 96,076, which is the subset. The 66 responses:

| | prompt | completion | total | cached |
| --- | ---: | ---: | ---: | ---: |
| sum | 117,081 | 5,385 | 122,466 | 31,537 |
| min / median / max | 1,603 / 1,672 / 1,992 | 43 / 71 / 346 | 1,657 / 1,864 / 2,120 | 0 / 507.5 / 1,604 |

`prompt_ms` sum 277,776.725, median 4,384.278. `predicted_ms` sum 173,419.363, median 2,275.452. Model time sum **451.196 seconds**. Wall sum 460.292 seconds, median 6.847 seconds. `predicted_per_second` median 30.688. No completion equalled 2,048. `wake_end` line 6045: `money` `not metered`, `estimated_usd` null, 67 call records (66 eye + 1 agent).

Slowest eye, jsonl line 2881: prompt 1,632, completion 346, total 1,978, cached 507, `prompt_ms` 3,651.463, `predicted_ms` 11,235.638. Model time 14.887 seconds.

Wake span: markdown 14:32:12–15:05:06 = 1,974 s. Ledger line 16: "used 33.0 of 40 min, stopped" (1,980 s). Tool mix is dominated by `request shell` (86 headings) and `eye` (52).

Cold prompt (cached_tokens 0, n=6) median `prompt_ms` 4,308.932. Warm (n=60) median 4,733.268. A cache of about 508 text tokens did not cut the median prefill. The fast outliers (`prompt_ms` 145.414 on line 3070) are the calls whose cached count is almost the whole prompt.

### 2.5 `20261027_151228` — 15:12:28, 40 minutes

Model: jsonl line 2 `grok-4.7-xhigh-fast`; init `Grok 4.7 256K Extra High Fast`. Markdown line 3 matches. Eye calls: 0. This wake is the rewrite, not the desktop loop. Ledger line 18: "used 31.2 of 40 min, failed_agent".

| Chat | Tools | Model calls | Wall | Exit | Tokens |
| --- | ---: | ---: | ---: | --- | --- |
| :1 | 42 | 17 | 1,051.140 s (15:12:34.542–15:30:05.682) | `rotate` line 6304 | none |
| :2 | 42 | 22 | 809.547 s | `rotate` line 11028 | none |
| :3 | 0 | 0 | — | `failed_agent` line 11033, return code 2 | none |

Stream `401b0c3cc4dd460bb405a9f6b574968f.stream` `timestamp_ms` span 1,047.603 s for chat :1, thinking characters 137,566, matching the markdown think body of that segment. Stream `83ad90706fbe4823a6321e4bdd928749.stream` span 805.399 s, thinking characters 105,495. Together the markdown think text is 243,061 characters. Prompt characters 18,866 and 18,878. Chat :3's stream `0c5080e15dbb436b8fcf2d0d23df1a58.stream` is 187 bytes of non-JSON. `wake_end` line 11034: `not metered`, 0 calls.

Markdown span 15:12:34–15:43:39 = 1,865 s. Tool mix: read 36, shell 21, edit 14, grep 5, glob 4, webSearch 2, getMcpTools 2. Chat :1 is the longest single mind session in the day (1,051.140 s) and it has no token total, because `rotate` killed it at 42 tools.

### 2.6 `20261027_155202` — 15:52:02, 40 minutes

No jsonl file. Model, two lines in the markdown: line 7 `grok-4.7-xhigh-fast`, line 11 `Grok 4.7 256K Extra High Fast`. Stream `130358e138e64b239689d2912068f9c5.stream` init repeats the display name, `prompt_run` 20261027_155202, `user_chars` 7,312, 42 tools, 33 model calls, `timestamp_ms` span 499.609 s, thinking characters 22,972. No `result`. The journal stored thinking as `chars=<N>`: 33 `think` headings. Those lines are 301 characters. The integers inside them sum to 22,972. Stream `130358e1…` thinking-delta text is also 22,972 characters. Truncation changed the file, not the generation.

Markdown: `minutes=10` is wrong for this wake; line 3 says `minutes=40.0`. Span 15:52:02–16:02:17 = 615 s. Ledger line 20: "used 10.2 of 40 min, failed_agent". Two chats: the first exits `rotate` at 16:01:24 after 562 s, 42 tools, 18 `eye` headings; the second exits `failed_agent` at 16:02:17 with 0 tools. Its stream `41ff88f1f93d4dd6a12fc0546793fb8a.stream` is 21 bytes. Eye heading gaps (1-second clock): 18 positive gaps, sum 137 s, median 7 s, max 11 s. Several gaps sit on a preceding `capture` heading, so the gap includes capture plus the model. No eye token object exists after the jsonl writer was removed. Money: not computed.

### 2.7 `20261027_160354` — 16:03:54, 40 minutes

No jsonl. Model on every chat heading: line 7 of the pattern `grok-4.7-xhigh-fast`, and each `start` body `Grok 4.7 256K Extra High Fast`. Six streams carry the same display name and `prompt_run` 20261027_160354.

| Chat start | Tools | Model calls | Stream span | Prompt chars | `result` |
| --- | ---: | ---: | ---: | ---: | --- |
| 16:03:54 | 42 | 24 (`d4345487…`) | 350.344 s | 7,519 | no |
| 16:10:01 | 42 | 27 (`2a6ad177…`) | 257.011 s | 7,953 | no |
| 16:14:26 | 42 | 28 (`88f4c4c5…`) | 420.493 s | 7,953 | no |
| 16:21:36 | 43 | 28 (`793b1130…`) | 350.186 s | 8,121 | no |
| 16:27:35 | 42 | 29 (`bcb65b11…`) | 294.688 s | 8,123 | no |
| 16:32:38 | 37 | 18 (`c23fbfd6…`) | 303.096 s | 8,303 | yes |

Last chat, stream line 1575:

> `"duration_ms": 306941, "duration_api_ms": 306941`
>
> `"usage": {"inputTokens": 114156, "outputTokens": 26063, "cacheReadTokens": 662016, "cacheWriteTokens": 0}`

Input+cache = 776,172, above 500k, so this is not one request. INFERRED Fast short-context list price on the sum: **$1.431396**. The first five chats have no token total. Journal think headings are all `chars=<N>` (155 headings). Those lines are 1,432 characters. The integers inside them sum to 175,011. The six streams' thinking-delta texts are 32,183, 24,137, 31,935, 29,804, 23,461, and 33,491, which also sum to 175,011.

Eye headings: 23. Positive gaps sum 172 s, median 7 s, max 10 s. No eye usage object. Markdown span 16:03:54–16:37:54 = **2,040 seconds**. Ledger line 22: "used 34.0 of 40 min, stopped". 34.0 minutes is 2,040 seconds. The two clocks match exactly. This is the longest wake wall in the set. The 20 percent line in that ledger sentence is 16:35:54, which is start plus 32 minutes.

### 2.8 `20261027_182210` — 18:22:10

No jsonl. Markdown line 7 `grok-4.7-xhigh-fast`, line 11 `Grok 4.7 256K Extra High Fast`. Stream `deaaedef559a4b3c9b793396d1b91f02.stream`: same model, `user_chars` 5,780, 28 tools completed, 16 model calls, thinking characters 19,444, `timestamp_ms` span 214.994 s. No `result`. No agent-exit heading. Markdown span 18:22:10–18:26:42 = 272 s. The 16 think headings are the lines `chars=<N>`, 149 characters in all. The integers sum to 19,444, and the stream thinking-delta text is 19,444 characters. Ledger.md has no lesson for this run id (line 22 is 160354, line 24 is 183727).

Eye headings: 6. First actions: `key` four times from 18:26:07, then the heading gap to 18:26:42 is 17 seconds; `terminate` twice at 18:25:06. No eye tokens. Money: not computed.

### 2.9 `20261027_183727` — 18:37:27, 5 minutes

No jsonl. Markdown line 3 `minutes=5.0`, line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. Stream `f1680f9dbc0544b9a5a0771184097188.stream` init matches, `user_chars` 7,761, 25 tools, 15 model calls, thinking characters 16,476. The markdown think bodies are also 16,476 characters and 0 of them are `chars=` truncations. Journal and stream match, unlike 182210.

Stream line 816:

> `"duration_ms": 266813, "duration_api_ms": 266813`
>
> `"usage": {"inputTokens": 45981, "outputTokens": 14292, "cacheReadTokens": 530048, "cacheWriteTokens": 0}`

Input+cache = 576,029, above 500k. INFERRED Fast short-context list price: **$0.885476**. Markdown span 18:37:27–18:42:06 = 279 s. Ledger line 24: "used 4.7 of 5 min, stopped". Exit heading `stopped`.

Eye headings: 8. First actions: `left_click` at `[104, 972]` (18:39:17), `unparsed` (18:39:35), `key`, another `unparsed` (18:40:17), `key`, `left_click` (18:41:03), `terminate` (18:41:22), `key`. Positive gaps sum 85 s, median 17 s, max 19 s. The 0-second gaps are a second heading written in the same second as the previous eye (the `key` lines). No eye token object. Ledger line 25 says the two `key` calls "were unparsed and typed nothing," which matches the `unparsed` headings.

### 2.10 `20261027_190548` — 19:05:48, 10 minutes

No jsonl. Line 3 `minutes=10.0`, line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. Two streams, neither has `result`:

- `550a6d96104d48109cf663e4e337bf5d.stream`: 42 tools, 29 model calls, prompt 10,211 chars, thinking 20,049 chars, span 324.810 s. Markdown exit `rotate` at 19:11:22, wall 334 s.
- `59afdf931c8e4a8fafa91065e7b966b8.stream`: 19 tools, 10 model calls, prompt 8,046 chars, thinking 11,123 chars, span 254.379 s. Exit `deadline` at 19:15:48, wall 265 s.

No mind tokens. Markdown span 19:05:48–19:15:48 = 600 s. Ledger line 26: "used 10.0 of 10 min, deadline". The wake used the whole budget and recorded no usage, because both endings kill the process (`rotate`, then `deadline`).

Eye headings: 27. A gap here is the heading's clock minus the previous heading of any kind. Positive gaps: 21, sum 209 s, median 9 s, max 17 s. Six of those gaps sit on a heading whose first action token is `left_click_drag`, and the gaps are 7, 8, 9, 17, 17, and 17 seconds. The mean of the two central values, 9 and 17, is 13 s. Two further headings also begin with `left_click_drag` and share the previous heading's second, so a first-token count of drag headings is 8, not 6. Bodies that contain exactly one action are `left_click` 4, `unparsed` 2, `terminate` 4, `left_click_drag` 6, `mouse_move` 4. The other 7 eye headings repeat an earlier log. The 19:15:41 body contains 8 actions. No per-call eye timings. Ledger line 27 is "No lesson was written."

### 2.11 `20261027_191902` — 19:19:02, 10 minutes

No jsonl. Line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. One chat. Stream `ea547f76d02442668ce35a2748a1601a.stream`: 37 tools, 24 model calls, prompt 9,033 chars, thinking 13,770 chars (markdown think bodies 13,770), span 512.539 s. Exit `stopped` at 19:27:48, markdown wall 526 s.

Stream line 720:

> `"duration_ms": 520086, "duration_api_ms": 520086`
>
> `"usage": {"inputTokens": 130621, "outputTokens": 13548, "cacheReadTokens": 955008, "cacheWriteTokens": 0}`

Input+cache = 1,085,629, above 500k. INFERRED Fast short-context list price: **$1.640068**. Markdown span 19:19:02–19:27:48 = 526 s. Ledger line 28: "used 8.8 of 10 min, stopped".

Eye headings: 23. Positive gaps sum 305 s, median 18 s, max 22 s. Drag and move gaps sit at 19–20 s (`left_click_drag` at 19:24:21, 19:25:01, and the moves around them). That 305 s is serial eye-thread time inside a 526 s wake, so it overlaps the mind rather than adding on top. No eye token object. Ledger lines 28–29 name the drags and say the stop check stayed open because terminate was unparsed until 19:27:06.

### 2.12 `20261027_194156` — 19:41:56, 10 minutes

No jsonl. This wake starts 23 seconds after commit `657392cd` (19:41:33), the eye-thread commit. Line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`.

- Chat 1, stream `ca2248dc1b464db2bde1adaa5b2d5234.stream`: 42 tools, 28 model calls, prompt 9,911 chars, thinking 25,324 chars, span 562.559 s. Exit `rotate` at 19:51:42. No `result`.
- Chat 2, stream `fd368b963e4e486387f7ba61984e0caa.stream`: 0 tools, 0 model calls, prompt 12,746 chars, thinking 408 chars, span 3.777 s. Exit `deadline` at 19:51:56. No `result`.

No mind tokens for either chat. Markdown span 19:41:56–19:51:57 = 601 s. Ledger line 30: "used 10.0 of 10 min, deadline".

Eye headings: 28. Of those, 17 bodies contain exactly one action and that action is `left_click` at `[500, 498]`: one at 19:47:15, then 16 consecutive from 19:47:31 through 19:51:57 (markdown lines of those headings begin at the 19:47:31 heading; the run of identical first-actions is the measurement). Span of the 17 calls: 19:47:15 to 19:51:57 = **282 seconds**. The consecutive 16: 19:47:31 to 19:51:57 = **266 seconds**. Positive eye gaps overall sum 215 s, and the left-click gaps in that stretch reach 16–19 s (19:51:19 to 19:51:38 is 19 s). Ledger line 31: "From 19:47:15 through 19:48:38 the stroke sentence was repeated left_click at [500, 498]." The ledger names the start. The headings continue the same coordinate through 19:51:57, past the ledger's "through 19:48:38". No eye tokens. This commit's code still executed the repeated click; see section 3.

### 2.13 `20261027_195316` — 19:53:16, 10 minutes

No jsonl. Line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. Wake starts 11 seconds after commit `233661f5` (19:53:05). One stream, `f886a5a6b50241f290f8bd00e9c67d3a.stream`: 32 tools, 23 model calls, prompt 13,286 chars, thinking 28,517 chars, span 584.627 s. Types include no `result`. Exit `deadline` at 20:03:17, markdown wall 600 s. Span 19:53:17–20:03:17 = 600 s. Ledger line 32: "used 10.0 of 10 min, deadline". No mind tokens.

Eye headings: 28. Exact-coordinate repeats are short: `[160, 741]` three times (19:55:07, 19:55:15, 19:55:23), `[12, 970]` twice, `[205, 611]` three times (19:59:14, 19:59:24, 20:02:36), `[207, 611]` twice, `[205, 609]` twice, `[266, 609]` twice. The 19:53 note fires only when action and coordinate are equal to the previous assistant turn. `[205, 611]` and `[205, 609]` are different coordinates, so the note does not apply. `[207, 611]` and `[205, 611]` are also different coordinates. Positive gaps sum 260 s, median 9 s, max 19 s. Ledger lines 32–33 describe these clicks and say "This wake has no record of an open display showing 4." No eye tokens.

### 2.14 `20261027_200412` — 20:04:12, 10 minutes

No jsonl. Line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. One stream, `886953864f9e4131997a0f93c5f8632f.stream`: 31 tools, 22 model calls, prompt 11,036 chars, thinking 25,806 chars (markdown matches), span 582.437 s. Exit `stopped`.

Stream line 1271:

> `"duration_ms": 588889, "duration_api_ms": 588889`
>
> `"usage": {"inputTokens": 78179, "outputTokens": 22031, "cacheReadTokens": 914432, "cacheWriteTokens": 0}`

Input+cache = 992,611, above 500k. INFERRED Fast short-context list price: **$1.491520**. Markdown span 20:04:12–20:14:06 = 594 s. Ledger line 34: "used 9.9 of 10 min, stopped".

Eye headings: 5, from 20:06:21 (`open`) to 20:07:04 (`terminate`). One `left_click` at `[225, 974]`, one `type`, one `key`, one `terminate`. The eye span is **43 seconds**. The mind `duration_ms` is 588,889, which is the whole chat (588.889 seconds). The terminate heading is 20:07:04. The stop heading is 20:13:51, **407 seconds** later. The mind heading is 20:14:06, 422 seconds after the terminate. Ledger line 35 quotes the terminate claim at 20:07:04 and says there was no second look. Positive eye gaps sum 16 s. No eye tokens.

### 2.15 `20261027_201442` — 20:14:42, 10 minutes

No jsonl. Line 3 `minutes=10.0`, line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. One stream, `e9d035b81d8a434c9f4e1759b2f906f3.stream`: 39 tools, 28 model calls, prompt 11,061 chars, thinking 27,193 chars (markdown matches), span 584.162 s. Exit `stopped`.

Stream line 1276, the largest `duration_ms` and the largest `cacheReadTokens` in the day:

> `"duration_ms": 590699, "duration_api_ms": 590699`
>
> `"usage": {"inputTokens": 107311, "outputTokens": 23585, "cacheReadTokens": 2132224, "cacheWriteTokens": 0}`

Input+cache = 2,239,535, far above 500k. INFERRED Fast short-context list price on the sum: **$2.844488**. This is the largest list-price equivalent among the eight chats. Markdown span 20:14:43–20:24:39 = 596 s. Ledger line 36: "used 9.9 of 10 min, stopped".

Eye headings: 29. `left_click` at `[283, 66]` four times (20:16:18 through 20:16:52). `left_click` at `[223, 66]` three times. `left_click` at `[283, 230]` **18 headings**, from 20:18:36 through 20:24:29, with one `key` at 20:20:07 in the middle (5 clicks, the key, then 13 clicks). Span of those 18 clicks: **353 seconds**. Inter-heading gaps on that coordinate run from 18 s up to 22 s (20:23:25 to 20:23:46 is 21 s; 20:23:46 to 20:24:07 is 21 s; 20:24:07 to 20:24:29 is 22 s). Quote of the first of them, markdown line 198 (heading line 196, `## 20:16:18 eye` is the earlier coordinate; the `[283, 230]` body is the same shape):

> `action=left_click coordinate=[283, 66] answer= thought=<think>`

The `[283, 230]` lines use that same sentence with the coordinate changed. Ledger line 37: "From 20:18 left_click [283, 230] repeated after the same-coordinate note and was still repeating at 20:23:49." Headings continue to 20:24:29. The 19:53 note was already in the code (section 3) and the clicks still ran. No eye tokens. The mind cache sum on this chat (2,132,224) is 2.42 times the next wake's cache sum (881,024). Tying that ratio to the click loop alone breaks: there is no per-call usage to show the log text entering each request. The co-occurrence is OBSERVED. The causal split is not.

### 2.16 `20261027_202606` — 20:26:06, 10 minutes

No jsonl. Starts 18 seconds after commit `1900589` (20:25:48). Line 3 `minutes=10.0`, line 7 `grok-4.7-medium-fast`, line 11 `Grok 4.7 256K Medium Fast`. `prompt.txt` line 181 is `grok-4.7-medium-fast` and line 4 names the end `20:36:06`, which is this wake. Stream `e0ae089f9b2c430080a6f430b7495d79.stream`: 33 tools, 21 model calls, prompt 16,797 characters, thinking 28,563 characters (markdown matches), span 583.440 s.

Stream line 1351:

> `"duration_ms": 589085, "duration_api_ms": 589085`
>
> `"usage": {"inputTokens": 129589, "outputTokens": 22590, "cacheReadTokens": 881024, "cacheWriteTokens": 0}`

Input+cache = 1,010,613, above 500k. INFERRED Fast short-context list price: **$1.670460**. Markdown span 20:26:06–20:36:01 = 595 s. Ledger line 38: "used 9.9 of 10 min, stopped".

Eye headings: 3. Line 267 body `open` at 20:28:13. Line 276: `action=left_click coordinate=[194, 972]`. Line 296: `action=terminate coordinate=None answer=Done — File Explorer is open to the folder you specified.` Span 20:28:13–20:28:28 = **15 seconds**. One click, no repeat. The mind `duration_ms` is 589,085. Same shape as 200412: the eye is done in well under a minute, and the mind session runs almost the whole 10 minutes. `prompt.txt` is 16,801 bytes. Its FARA section (lines 22–164) is the previous wake's repeated Notepad clicks, pasted into this wake's prompt. That is input characters. Their token count was not recorded separately from the chat sum.

## 3. Before versus after, tied to commit timestamps

All AuthorDates below are 27 October 2026, +0100, from `git log --format=fuller`. The running process loads `organism.py` at wake start, so a commit applies to wakes that start after it.

### 3.1 15:42:51 — `9823582`, collapse, meter removed

Last wake that still writes jsonl: 151228, `wake_end` at 15:43:39. First wake with no jsonl: 155202 at 15:52:02. `git log -S estimated_usd` and `git log -S .jsonl` both name this commit. After it, eye `usage` and `timings` stop. The only mind tokens after 15:42 are the `result` lines inside `streams\`, and only when the process was not killed first.

Prompt characters drop across the same boundary. 151228 prompts are 18,866 and 18,878 characters. 155202's surviving prompt is 7,312. 160354 prompts are 7,519–8,303. 182210 is 5,780. That is OBSERVED co-timing with the collapse. The commit message says the body became one file. It does not, by itself, prove which sentences were deleted from the prompt. Confidence that the prompt got shorter: high. Confidence that this one commit is the whole cause: medium.

### 3.2 18:37:15 — `26f2fa07`, full thought text put back in the journal

182210 (start 18:22:10, before the commit): the `chars=` lines are 149 characters and the integers inside them sum to 19,444, matching the stream thinking text of 19,444 characters. Markdown file 3,856 bytes, span 272 s.

183727 (start 18:37:27, twelve seconds after the commit): journal think text 16,476 characters, stream thinking text 16,476 characters, markdown file 173,499 bytes, span 279 s.

The generation size was already about 19k characters before the commit. The journal stopped throwing it away. `render_prompt` (organism.py lines 796–806) puts heading counts into the next prompt, not the journal body. A larger journal raises the cost of a later `read` of that file. Those read-tool token counts are inside the chat sums when a `result` exists, and are invisible when it does not. The chain from "store the full thought" to "the next prompt's input tokens" breaks at `render_prompt`.

`02cf66dd` at 19:05:12 drops duplicate shell parse fields. 183727 tool heading keys include `hookAdditionalContexts`. 190548 tool heading keys do not. That is a smaller journal record. 190548 has no `result`, so the dollar effect is unmeasured.

### 3.3 19:02:57 — `c3887eba`, stop rule from the last 20 percent to the last 5 percent

160354, under the older rule, ledger line 22: used 34.0 of 40 minutes, and names the 20 percent line at 16:35:54. 183727, ledger line 24: used 4.7 of 5, and the lesson says the wake was past the 20 percent line.

After 19:02:57, the 10-minute wakes use 10.0 (190548, 194156, 195316) or 9.9 (200412, 201442, 202606). Heading spans sit at 594–601 seconds against a 600-second budget. The extra time against an 80 percent stop is on the order of one to two minutes of a 10-minute wake. 190548 ran all 600 seconds and has no usage object, so the money of that extra time is not in the files.

### 3.4 19:41:33 — `657392cd`, eye thread beside the mind

Commit message, verbatim: "Planned: Grok --> one sentence --> eye call (blocks, max 8) --> log --> Grok" and "Actual, wakes 190548 and 191902" ending in the local model idle while Grok thinks. Current code lines 939–940 start the eye thread, and lines 678–685 return the log without waiting when the thread is live.

Before, measured eye model time is on the mind's critical path. 143208's 66 eye calls took 451.196 seconds of `prompt_ms`+`predicted_ms` inside a 1,974-second wake. Median wall of one call was 6.847 seconds, and the mind's `--eye` tool could not return until the call did.

After, 200412's eye headings occupy 43 seconds and the mind `duration_ms` is 588,889. 202606's eye headings occupy 15 seconds and the mind `duration_ms` is 589,085. The eye's seconds no longer add to the wake wall. They still occupy the machine at `192.168.16.31`. When the eye does not terminate, the overlap is the other way: 201442's `[283, 230]` headings cover 353 seconds inside a 596-second wake, concurrent with `duration_ms` 590,699.

### 3.5 19:53:05 and 20:25:48 — the repeated click

`233661f5` at 19:53:05 changes the text written after `execute`. The diff replaces `put('user', text=f"Done. action={seen['action']} coordinate={coord}")` with a note appended to that same Done string: "That action already happened at this coordinate." The click is stored and executed first. The note does not skip it.

`1900589` at 20:25:48 moves the test above `execute` and `continue`s:

```649:650:organism.py
        if prior and prior.get('action') == seen['action'] and prior.get('coordinate') == coord:
            put('user', text=f"Not done. {seen['action']} at {coord} already happened. Choose another action or another coordinate."); continue
```

| Wake | Relation to the two commits | Same-coordinate eye headings | Span of that run |
| --- | --- | --- | --- |
| 194156 | before both | 17 × `left_click` `[500, 498]` | 282 s |
| 195316 | after the note, before the skip | 3 × `[160, 741]`; pairs at nearby but unequal coordinates | gaps median 9 s |
| 201442 | after the note, before the skip | 18 × `left_click` `[283, 230]` | 353 s |
| 202606 | after the skip | 1 × `left_click` `[194, 972]`, then terminate | 15 s |

The note commit did not remove the repeated-click time. The skip commit's first wake has one click. Mind `duration_ms` does not follow: 201442 is 590,699 ms and 202606 is 589,085 ms. Skipping the clicks saved eye cycles. It did not shorten the mind session. List price moved from $2.844488 to $1.670460 on the Fast short-context reading, a difference of $1.174028, almost all of it `cacheReadTokens` (2,132,224 versus 881,024). That dollar gap is INFERRED, and it is not isolated from the rest of the chat (different sentence, different prompt length: 11,061 versus 16,797 characters).

### 3.6 What did not change the published price

`grok-4.7-xhigh-fast` (through 182210) and `grok-4.7-medium-fast` (from 183727) are both Fast. The Cursor model page prices Fast once. It prices effort as time, not as a second rate. Output tokens on the priced chats do not separate cleanly by effort: the xhigh results that exist are the short final chats (output 30,941, 21,767, 26,063) and the medium 10-minute results are 13,548 to 23,585. The long xhigh chats were rotated away before `result`, so their output tokens are unknown. A claim that Extra High cost more dollars per token is not supported by either page.

## 4. Symptom / root-cause chains

### 4.1 Most chats have no dollar figure

Symptom. 22 chats that printed a model `init` have no `usage` object. The five rotate chats of 160354, both chats of 190548, both chats of 194156, and the single chats of 195316, 151228 (:1 and :2), 141013, 142947, 155202, and 182210 are in that set. 133306 chats :1–:3 and 143208 chats :1–:3 are too.

Root cause. `read_agent` kills the process on `rotate` (organism.py 902–903 via 854–857) and on `deadline` (860–861). The usage object is printed by the CLI as `type=result`, which arrives only if the process lives long enough. Evidence that the chain holds: every stream with `result/success` belongs to a chat whose markdown exit is `stopped` (183727, 191902, 200412, 201442, 202606, 160354's last chat, 143208 chat :4, 133306 chat :4). Every stream whose types end at `tool_call/completed` with no `result` belongs to `rotate` or to a hard stop. The chain does not break.

Consequence. The $12.084300 sum covers 8 chats. The longest chat of the day, 151228 chat :1 at 1,051.140 seconds (jsonl) and 1,047.603 seconds (stream timestamps), is entirely outside that sum.

### 4.2 A finished eye task still pays for a full mind session

Symptom. 200412 eye headings run 20:06:21–20:07:04 (43 s) while `duration_ms` is 588,889. 202606 eye headings run 20:28:13–20:28:28 (15 s) while `duration_ms` is 589,085.

Root cause. The prompt forbids stopping on the eye's terminate, and the stop rule waits for the last 5 percent. organism.py line 24: "A terminate answer is her claim. Do not --stop because she says the task is done. --stop only in the last 5 percent, after the story and one ledger lesson." `prompt.txt` lines 3–4 repeat that sentence for the 20:26 wake, including "The wake has 10 minutes and ends at 20:36:06." Ledger line 35 records the 20:07:04 terminate and the absence of a second look, while line 34 records 9.9 of 10 minutes used. The chain holds. The time cost of "do not stop early" is the rest of the mind session, about 9 minutes after a 43-second eye task in 200412. The money cost is that session's token sum, not the eye.

### 4.3 Repeated clicks burn eye time; the 19:53 note did not stop them

Symptom. 194156: 17 eye headings whose only action is `left_click` `[500, 498]`, spanning 282 s, with cycles of 16–19 s near the end. 201442: 18 headings of `left_click` `[283, 230]`, spanning 353 s, cycles 18–22 s.

Root cause of the clicks still running after 19:53. Commit `233661f5` appends a sentence to the Done observation after `execute`. Commit `1900589` is the one that `continue`s before `execute`. 201442 starts at 20:14:42, between those commits, and the ledger (line 37) says the click "was still repeating at 20:23:49." The headings reach 20:24:29. The chain holds.

Where the money chain breaks. Those cycles are local eye calls. The afternoon eye calls that have timings cost about 7 seconds and about 1,900 tokens each, and the meter marked them `not metered`. The evening cycles are 18–22 seconds and have no `prompt_ms`, so the GPU share of the 353 seconds is unmeasured. The mind's higher cache sum on 201442 versus 202606 sits beside the click loop. Without per-call usage, the clicks cannot be given a dollar share of the $2.844488.

### 4.4 Eye prefill stays slow because the screenshot is not the cached prefix

Symptom. On 81 timed calls (15 + 66), median `prompt_ms` is 4,818 ms and 4,384 ms. Median `predicted_per_second` is 30.6 and 30.7. Median total tokens are 1,981 and 1,864. Completion never reached `max_tokens` 2048 (max completion 416 and 346).

Root cause, with one inference. `cached_tokens` median is 508 and 507.5 against prompt token medians of 1,918 and 1,672. Warm median prefill is not lower than cold median prefill (4,733 ms versus 4,309 ms on 143208). The calls that do drop `prompt_ms` under 150 ms are the ones whose cached count is almost the entire prompt (133306 line 5760, cached 1,635 of 1,639 prompt tokens, `prompt_ms` 79.657). `ask()` attaches the current screenshot as an `image_url` on every turn (lines 507–510) and only reuses image bytes for turns in the last three shots. INFERRED, confidence medium: the uncached majority of the ~1,900 prompt tokens is the image, so a 508-token text cache cannot move the median. The chain from "cache is only ~508" to "median prefill stays ~4.5 s" is OBSERVED. The identification of the remainder as image tokens is the inference.

Money. Sum of the 81 calls: 151,864 total tokens (29,398 + 122,466) and 560.333 seconds of model time (109.137 + 451.196). Every corresponding component says `money` `not metered` and `estimated_usd` null. The Hugging Face card and the llama.cpp license state no per-token price. Later eye calls, which have no timings, are not added to the 560.333 seconds.

### 4.5 Rotation throws away the usage line and starts a new prompt

Symptom. Chat exits with `"reason": "rotate", "completed_calls": 42` and `"returncode": 1` (133306 jsonl line 3444–3445, and the same pair on 141013, 143208, 151228, and the markdown `agent-exit` bodies `rotate` on 155202, 160354, 190548, 194156). The next `chat_prompt` is a new character count (133306 chat 2 is 17,340 characters after chat 1's 21,088).

Root cause. Lines 902–903 plus the kill in `finish`. The chain holds, and it is the same mechanism as section 4.1. The second cost is the new prompt. Its character length is measured. Its token length is not, except insofar as a later `result` sums a whole chat that began with that prompt. 160354's five rotated chats each sent a 7,519–8,123 character prompt and then died without a usage line. Their list price is unknown.

### 4.6 Journal truncation was not a token saving

Symptom. 155202, 160354, and 182210 store `chars=<N>` in think headings. The lines themselves are 301, 1,432, and 149 characters. The integers sum to 22,972, 175,011, and 19,444. The matching stream thinking-delta texts are those same three totals.

Root cause of the small files: the journal writer of that period counted instead of copying. Root cause of the tokens: the agent had already emitted the deltas into the stream before the journal shortened them. After `26f2fa07`, 183727's journal and stream both hold 16,476 thinking characters. The chain from truncation to a smaller mind bill breaks. The billable text had already been generated.

## 5. Byte-count appendix

Every file whose bytes were read in full. Line counts are newline-separated records (a final line without a newline is counted). Jsonl and stream lines were each passed to a JSON parser; markdown lines were each tested against the heading pattern.

### Run markdown

| File | Bytes | Lines |
| --- | ---: | ---: |
| `run_20261027_133306.md` | 653031 | 3220 |
| `run_20261027_141013.md` | 183382 | 1251 |
| `run_20261027_142947.md` | 26575 | 170 |
| `run_20261027_143208.md` | 715069 | 3232 |
| `run_20261027_151228.md` | 539109 | 2573 |
| `run_20261027_155202.md` | 9114 | 500 |
| `run_20261027_160354.md` | 26017 | 2044 |
| `run_20261027_182210.md` | 3856 | 236 |
| `run_20261027_183727.md` | 173499 | 588 |
| `run_20261027_190548.md` | 307577 | 1156 |
| `run_20261027_191902.md` | 185642 | 949 |
| `run_20261027_194156.md` | 174843 | 877 |
| `run_20261027_195316.md` | 176476 | 823 |
| `run_20261027_200412.md` | 142048 | 598 |
| `run_20261027_201442.md` | 310720 | 889 |
| `run_20261027_202606.md` | 136548 | 608 |

Markdown total: 3,763,506 bytes, 19,714 lines.

### Run jsonl

| File | Bytes | Lines |
| --- | ---: | ---: |
| `run_20261027_133306.jsonl` | 5313837 | 9961 |
| `run_20261027_141013.jsonl` | 1445020 | 5547 |
| `run_20261027_142947.jsonl` | 170471 | 577 |
| `run_20261027_143208.jsonl` | 2174503 | 6046 |
| `run_20261027_151228.jsonl` | 2857609 | 11035 |

Jsonl total: 11,961,440 bytes, 33,166 lines, 0 bad lines. No `run_20261027_155202.jsonl` through `run_20261027_202606.jsonl`.

### Streams

| File | Bytes | Lines |
| --- | ---: | ---: |
| `streams\0b10ead822cc4433946996d2ad935fa5.stream` | 143326 | 572 |
| `streams\0c5080e15dbb436b8fcf2d0d23df1a58.stream` | 187 | 1 |
| `streams\130358e138e64b239689d2912068f9c5.stream` | 586240 | 1104 |
| `streams\2a6ad17738a24b4a851c43d76d048556.stream` | 420893 | 1168 |
| `streams\2bcbdcb455c34a87bbd5e271c4b39050.stream` | 493934 | 1261 |
| `streams\3e8984ce791c4490be2070da33d1170a.stream` | 183 | 1 |
| `streams\401b0c3cc4dd460bb405a9f6b574968f.stream` | 2488082 | 6299 |
| `streams\41ff88f1f93d4dd6a12fc0546793fb8a.stream` | 21 | 1 |
| `streams\550a6d96104d48109cf663e4e337bf5d.stream` | 379508 | 1015 |
| `streams\59afdf931c8e4a8fafa91065e7b966b8.stream` | 259757 | 550 |
| `streams\793b11300ec14ebd81fa4e1d9ecc2f27.stream` | 458033 | 1462 |
| `streams\7e394b3b958646779b2e8a50ddb9a5a4.stream` | 358249 | 1047 |
| `streams\83ad90706fbe4823a6321e4bdd928749.stream` | 1421918 | 4719 |
| `streams\886953864f9e4131997a0f93c5f8632f.stream` | 364824 | 1271 |
| `streams\88f4c4c5eb354fcbaef8f9456f1e25cb.stream` | 511042 | 1520 |
| `streams\a1395e425dec4c3baf784917c1e918c8.stream` | 910423 | 1780 |
| `streams\a9ba1244f0484e618d47de74d9edf2fc.stream` | 595005 | 1640 |
| `streams\bcb65b11a206413ca3925c2315e2b3f5.stream` | 410028 | 1157 |
| `streams\c23fbfd6b7bc48a5840970873891a884.stream` | 459437 | 1575 |
| `streams\ca2248dc1b464db2bde1adaa5b2d5234.stream` | 421471 | 1191 |
| `streams\d4345487680e4d869b9047aa63e6c0a9.stream` | 462714 | 1469 |
| `streams\d5cd315a8aec4383957db3b3f2c544bd.stream` | 1278033 | 5532 |
| `streams\deaaedef559a4b3c9b793396d1b91f02.stream` | 283236 | 882 |
| `streams\e0ae089f9b2c430080a6f430b7495d79.stream` | 391168 | 1351 |
| `streams\e9d035b81d8a434c9f4e1759b2f906f3.stream` | 595270 | 1276 |
| `streams\ea547f76d02442668ce35a2748a1601a.stream` | 355751 | 720 |
| `streams\f1680f9dbc0544b9a5a0771184097188.stream` | 322658 | 816 |
| `streams\f886a5a6b50241f290f8bd00e9c67d3a.stream` | 474911 | 1378 |
| `streams\fd368b963e4e486387f7ba61984e0caa.stream` | 17946 | 30 |

Named by `run_20261027_133306.jsonl` and not on disk, so not read: `streams\b6b060e27b2841adb5a0d5ca01b5f7ff.stream`, `streams\8fbb8d07d2ba49fa8150dcee79ada97b.stream`, `streams\66fa013276bf40949fde422d53148811.stream`, `streams\2d91cb65ada34683820f3cd39d610cf4.stream`.

### Source, memory, and the price extract

| File | Bytes | Lines |
| --- | ---: | ---: |
| `organism.py` | 65412 | 1044 |
| `main.py` | 123 | 3 |
| `ledger.md` | 12147 | 39 |
| `prompt.txt` | 16801 | 200 |
| `skills\.gitkeep` | 0 | 0 |
| Cursor models-and-pricing extract read locally (`f11f4aac-29a2-4c7b-b0e5-c7e69eaa548f.txt`) | 22060 | 215 |

`evidence.py` at commit `8e3e4837` was read through `git show` (136 lines). It is not a working-tree file. The web pages in section 1 were read as fetched documents, not as workspace files.

`edit_log.txt`: absent.

## 6. Consultation

The four reports were read on 27 October 2026. They were still being revised during that reading, so each file was read once as a body and again for the consultation section and the timeline sentences that section rewrote. Final sizes after that second read: `reports\01-systems.md` (77,289 bytes, 607 lines), `reports\02-prompt.md` (88,468 bytes, 607 lines), `reports\03-behavior.md` (80,515 bytes, 774 lines), `reports\05-evolution.md` (64,993 bytes, 572 lines). Where one of them states a duration, a token total, or a price, the run, the jsonl, or the stream was measured again. A list price is not an invoice. OBSERVED means the quote is in a file or on a page opened this day. INFERRED means an arithmetic step the file does not perform.

### 6.1 Agreements

The four reports and this one use the same wake clocks where they name the same object.

- Ledger minutes and heading spans match. 160354 is 16:03:54–16:37:54, 2,040 seconds, ledger "34.0 of 40". 133306's stop heading, line 3157, is `used_seconds` 1955.317, `remaining_seconds` 444.683, `left` "7.4 min", `now` "14:05:41". `01-systems.md` quotes that object. `02-prompt.md` quotes 444.683. The mind line is still 33.0 minutes and the jsonl wake span is still 1,978.28 seconds. Those are the seal and the process clock. The stop object is the earlier instant.
- `02-prompt.md` quotes 143208's stop as 429.442 seconds left. `run_20261027_143208.md` line 3185: `"used_seconds": 1970.558, "remaining_seconds": 429.442, "left": "7.2 min"`. `01-systems.md` quotes the "7.2 min" field of the same object.
- 155202's second chat is 16:01:24 to 16:02:17, 53 seconds. `01-systems.md` states 53 seconds. The stream of that chat is still the 21-byte file.
- `01-systems.md` and this report count three chats in 151228. Jsonl line 2 is `chat_prompt` `:1`, line 6305 `:2`, line 11029 `:3`. Line 11032 is `chat_end` reason `failed_agent`, `completed_calls` 0.
- Eye headings across the 16 journals sum to 262 (12+52+18+23+6+8+27+23+28+28+5+29+3, with 141013, 142947, and 151228 at zero). `03-behavior.md` states 262.
- Full think bodies match `03-behavior.md` on every wake that stored the thought: 217,155, 123,455, 11,780, 123,672, 243,061, 16,476, 31,172, 13,770, 25,732, 28,517, 25,806, 27,193, and 28,563 characters.
- On the three wakes that stored `chars=<N>`, `03-behavior.md` added N. A fresh sum of those integers is 22,972, 175,011, and 19,444. The thinking-delta texts in the streams are the same three totals. 155202's one stream is 22,972. 182210's one stream is 19,444. 160354's six streams are 32,183, 24,137, 31,935, 29,804, 23,461, and 33,491. `03-behavior.md`'s day total of 1,133,779 characters is the full-text wakes plus these three integer sums. OBSERVED. The arithmetic that reaches 1,133,779 is INFERRED from that report's own per-wake figures plus the 19,444, which that report folds into the total and does not print alone.
- Seventeen `left_click` headings at `[500, 498]` in 194156, from 19:47:15 (line 513) through 19:51:57 (line 866). `02-prompt.md` lists those seventeen times. `01-systems.md` counts one and then sixteen further.
- Eighteen `left_click` headings at `[283, 230]` in 201442. `02-prompt.md` starts that run at 20:18:36. The first heading is line 446, `## 20:18:36 eye`. The last is line 838, `## 20:24:29 eye`.
- `01-systems.md` section 4.6: the `wake_end` payloads say `money` `not metered`, and "No dollar figure is claimed." That matches section 1. No report in this set prints an invoice, a pool balance, or a charged total. Their consultation remeasured the eight `usage` objects and quotes the same integers this report quotes, including 133306 jsonl line 9957 (`inputTokens` 73067, `outputTokens` 30941, `cacheReadTokens` 292608) and 201442's `cacheReadTokens` 2132224.
- After their own consultation pass, `01-systems.md` line 198 dates the eighteen `[283, 230]` headings from 20:18:36, line 446, and calls 20:18:54 the second of them. `01-systems.md` line 143 prints both the first-action scan (left_click 8, left_click_drag 8, mouse_move 5) and the single-action counts (4, 6, 4). `03-behavior.md` section 3.8 says seventeen of the nineteen `left_click` headings are `[500, 498]`. Those revised sentences match the recounts in sections 2.10, 2.12, and 2.15.
- `03-behavior.md` measures 20:07:04 to 20:13:51 as 407 seconds. That is the stop heading minus the terminate heading in `run_20261027_200412.md` (lines 324 and 539). `duration_ms` 588889 remains the whole chat.

### 6.2 Prices opened again

`02-prompt.md` is the only one of the four that cites a model price. Opened again 27 October 2026.

- https://docs.x.ai/developers/grok-4-7 — the page still shows "Input price | $2.00 / 1M tokens" and "Output price | $6.00 / 1M tokens". The Fast paragraph still contains "It costs 2x the standard token rates". On this open the same sentence continues ", or 1.5x for long-context requests." `02-prompt.md` quotes the 2× clause and the $2.00 / $6.00 pair. The extracted text returned on this open does not contain the words "Last updated: October 10, 2026". That date line remains SINGLE-SOURCE to `02-prompt.md`'s earlier fetch.
- https://cursor.com/docs/models-and-pricing — the table still shows Grok 4.7 input `$2`, cache read `$0.5`, output `$6`, and Grok 4.7 (Fast) input `$4`, cache read `$1`, output `$12`. `$0.5` is the same rate `02-prompt.md` writes as $0.50. The row Grok 4.7 500k (Fast) is still `$6` / `$1.5` / `$18`. The Fast row note still says long context above 256k "is billed at 2x standard rates", and the model page still says Fast long context "bill at 3x the standard rates". That conflict is the one already recorded in section 1. It is still on both pages.
- https://cursor.com/docs/models/grok-4-7 — still "$2/M input tokens, $0.50/M cached input tokens, and $6/M output tokens. Fast is $4/M input, $1/M cached, and $12/M output."
- https://docs.x.ai/developers/pricing — the Fast table is still below 200k `$4.00` / `$1.00` / `$12.00` and above 200k `$6.00` / `$1.50` / `$18.00`, with the prose "2x the standard token rates, or 1.5x for long-context requests."

`02-prompt.md`'s consultation, the same day, quotes the same two long-context sentences: Cursor Fast above 256k at 3× the standard rates, and xAI Fast above 200k at $6.00 / $1.50 / $18.00. The short-context Fast row is $4 / $1 / $12 on both pages. The threshold, 256k on Cursor and 200k on xAI, is still the split in section 1.

No dollar figure in section 1 was replaced. The eight-chat list-price sum stays **$12.084300**, INFERRED, Fast short-context additional usage, and it is not an invoice. `01-systems.md` and `05-evolution.md` did not open a price page in their consultation. `03-behavior.md` cites no model token price.

### 6.3 Disagreements kept

The first drafts of the other reports dated `[283, 230]` from 20:18:54, called nineteen clicks one point, and printed a first-action eye count without saying it included echoes. Their consultation sections rewrote those sentences. The revised lines match this report, so those three are not kept.

One count is still printed differently.

`05-evolution.md` line 48, the 151228 row, says **2 `start` headings**. The markdown has two `start` headings. The jsonl has three `chat_prompt` lines (2, 6305, 11029) and three `chat_end` lines. The third ends `failed_agent` with `completed_calls` 0. Chat 2's `chat_end` is line 11027, `at` 1793112219.2943015; chat 3's `chat_end` is line 11032. Their consultation says they did not re-open that jsonl and leaves the third process as this report's observation. This report keeps 3 chats. `01-systems.md` also says three chats. The "2" is the start-heading count. It leaves out the spawn that died before a `start` heading.

### 6.4 Corrections to the earlier text

The dollar arithmetic was not rewritten. The passages below were.

Think size on 155202, 160354, and 182210. The earlier sentences gave 301, 1,432, and 149 characters. A re-read shows those numbers are the lengths of the literal `chars=<N>` lines (301, 1,432, and 149, with the blank lines under the headings excluded). They are not the thoughts. The integers inside the lines sum to 22,972, 175,011, and 19,444, and the stream thinking-delta texts sum to the same figures. Sections 2.6, 2.7, 2.8, 3.2, and 4.6 now state both. What changed is the 160354 total: the earlier sentence gave two examples, 32,183 and 31,935, and did not print the six-stream sum 175,011. Why: `03-behavior.md` had added N, and that sum is the quantity the streams confirm.

190548 drag gaps. Section 2.10 already said median 13 s with n=6. The remeasure reproduces that 13 as the mean of 9 and 17 inside the six positive gaps 7, 8, 9, 17, 17, 17. The sentence now prints those six gaps, the first-token counts, and the single-action counts. The 13 seconds was not replaced. `01-systems.md` line 143 now prints the same two counts.

195316 coordinate `[205, 611]`. Section 2.13 said that point twice. The single-action headings are three: 19:59:14 line 556, 19:59:24 line 570, and 20:02:36 line 744. `01-systems.md` line 183 lists those three times. The earlier "twice" is withdrawn.

200412 after the eye. Section 2.14 already had the eye span of 43 seconds and `duration_ms` 588,889. It now also states the stop heading at 20:13:51, 407 seconds after 20:07:04, and the mind heading at 20:14:06, 422 seconds after the terminate. Why: `03-behavior.md` measured 407 seconds for the wait, and 588,889 milliseconds is the whole chat (588.889 seconds), not that wait.
