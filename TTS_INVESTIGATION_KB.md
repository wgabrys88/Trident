# TTS Voice Duplication Investigation - Knowledge Base

## Repository Information

### Repositories (both on `v3-optimization` branch)
- **chatterbox.cpp**: `C:\Users\eb-wjt\Downloads\chatterbox.cpp`
  - Git remote: `https://github.com/wgabrys88/chatterbox.cpp.git`
  - Current HEAD: `335a94eb9d1211bee1b31e9df8bae24c684494dc`
  
- **Trident**: `C:\Users\eb-wjt\Downloads\Trident`
  - Git remote: `https://github.com/wgabrys88/Trident.git`
  - Current HEAD: `753b7c13ff6f4dfe9d5f8c7e52e2b0a8f3d4c1e9`

---

## Problem Statement

**Original Issue**: TTS voice duplication - V3/Turbo/Nano repeat at pieces 19/23/25
**User Observation**: Audio output shows "9 10 twenty, eleven" - scrambled ordering
**Latest Observation**: 1-30 numbers ARE played, but repetitions are still heard

---

## Model Architecture Comparison

### Model Configuration (from Trident/*.py)

| Model | T3 File | S3Gen File | repeat-penalty | cfg-weight | context | cfm-steps |
|-------|---------|------------|----------------|------------|---------|------------|
| **Nano** | `chatterbox-t3-nano-q4_0.gguf` | `chatterbox-s3gen-nano-q4_0.gguf` | 1.2 | 0 | 2048 | 1 |
| **Turbo** | `chatterbox-t3-turbo-q4_0.gguf` | `chatterbox-s3gen-turbo-q4_0.gguf` | 1.2 | 0 | 8192 | 2 |
| **V3** | `chatterbox-t3-mtl-q4_0.gguf` | `chatterbox-s3gen-mtl-q4_0.gguf` | 1.5 | 0.3 | 2048 | 5 |

### Key Finding
- **Nano and Turbo already have CFG disabled** (`cfg-weight=0`)
- **V3 has CFG enabled** (`cfg-weight=0.3`)
- The original hypothesis that CFG was causing issues was **incorrect**

### Code Path Differences

**V3 (MTL variant)**: Uses `CHBX_VARIANT_MTL`
- Tokenizer: `mtl_tokenizer` (see `mtl_tokenizer.h/cpp`)
- Sampling: `sample_next_token_mtl()` in `t3_mtl.cpp`
- Has `fifth_consecutive()` repeat detection
- Has CFG (Classifier-Free Guidance) calculation

**Nano/Turbo (Non-MTL variant)**: Uses `eval_step()` and `sample_next_token_ex()`
- Tokenizer: `gpt2_bpe` (see `gpt2_bpe.cpp`)
- Sampling: `sample_next_token_ex()` in `main.cpp`
- **NO repeat detection** until our changes
- **No CFG** (cfg_weight is 0)

---

## Code Files Modified

### chatterbox.cpp/src/chatterbox_engine.cpp

**Location**: `C:\Users\eb-wjt\Downloads\chatterbox.cpp\src\chatterbox_engine.cpp`

**Changes made**:
1. Added `fifth_consecutive()` function (lines 37-43) - checks for 5 consecutive same tokens
2. Added comprehensive logging at generation start (line ~210):
   ```cpp
   fprintf(stderr, "t3.start: text_len=%d text='%.80s...' n_predict=%d repeat_penalty=%.2f cfg_weight=%.2f temp=%.2f top_k=%d top_p=%.2f\n", ...);
   ```
3. Added text tokenization logging (line ~225):
   ```cpp
   fprintf(stderr, "t3.tokenized: text_tokens_count=%d first_5=[%d,%d,%d,%d,%d] last_5=[%d,%d,%d,%d,%d]\n", ...);
   ```
4. Added fifth_consecutive detection for Nano/Turbo path (line ~280):
   ```cpp
   if (fifth_consecutive(out, token)) {
       repeat_token = token; repeat_stopped = true;
       fprintf(stderr, "t3.repeat_detected: token=%d position=%zu repeat_penalty=%.2f\n", ...);
       token = model.hparams.stop_speech_token;
   }
   ```
5. Added adjacent repeat detection logging:
   ```cpp
   if (out.size() >= 2 && out[out.size()-1] == out[out.size()-2]) {
       fprintf(stderr, "t3.adjacent_repeat: position=%zu token=%d\n", out.size(), token);
   }
   ```
6. Added token progress logging every 10 tokens:
   ```cpp
   if (out.size() % 10 == 0) {
       fprintf(stderr, "t3.token_progress: position=%zu token=%d logits_max=%.4f\n", ...);
   }
   ```

### chatterbox.cpp/src/main.cpp

**Location**: `C:\Users\eb-wjt\Downloads\chatterbox.cpp\src\main.cpp`

**Changes made** to `sample_next_token_ex()` (lines 521-585):

1. Added top-5 token logging for first 6 positions:
   ```cpp
   if (generated.size() <= 5) {
       // Find and log top-5 tokens by logit value
       fprintf(stderr, "t3.top5: pos=%zu [(%d,%.3f),(%d,%.3f),...]\n", ...);
   }
   ```

2. Added selected token logging:
   ```cpp
   fprintf(stderr, "t3.selected: pos=%zu token=%d temp=%.2f\n", generated.size(), selected_token, params.temp);
   ```

### chatterbox.cpp/src/t3_mtl.cpp

**Location**: `C:\Users\eb-wjt\Downloads\chatterbox.cpp\src\t3_mtl.cpp`

**Changes made** to `sample_next_token_mtl()` (lines 1132-1228):

1. Added top-5 token logging for first 6 positions:
   ```cpp
   fprintf(stderr, "t3.mtl_top5: pos=%zu [(%d,%.3f),(%d,%.3f),...]\n", ...);
   ```

2. Added selected token logging:
   ```cpp
   fprintf(stderr, "t3.mtl_selected: pos=%zu token=%d\n", generated.size(), (int32_t)i);
   ```

### Trident/main.py

**Location**: `C:\Users\eb-wjt\Downloads\Trident\main.py`

**Changes made** to `synthesize()` method (lines 233-277):

1. Added diagnostic logging for piece sending and audio assembly
2. Fixed socket option typo (was `IPPROTO_TCP_NODELAY`, should be `TCP_NODELAY`)

---

## Logging System

### C++ Logging Prefixes

All C++ logging goes to stderr and is captured in the runtime log file.

| Log Prefix | Location | What It Logs |
|------------|----------|--------------|
| `t3.start` | chatterbox_engine.cpp | Generation start with all params |
| `t3.tokenized` | chatterbox_engine.cpp | Text tokenization results |
| `t3.top5` | main.cpp | Top-5 tokens by logit before sampling (Nano/Turbo) |
| `t3.mtl_top5` | t3_mtl.cpp | Top-5 tokens by logit before sampling (V3) |
| `t3.selected` | main.cpp | Selected token after sampling (Nano/Turbo) |
| `t3.mtl_selected` | t3_mtl.cpp | Selected token after sampling (V3) |
| `t3.repeat_detected` | chatterbox_engine.cpp | Fifth consecutive token detected |
| `t3.adjacent_repeat` | chatterbox_engine.cpp | Two consecutive same tokens |
| `t3.token_progress` | chatterbox_engine.cpp | Progress every 10 tokens |

### Python Logging Prefixes

| Log Prefix | Location | What It Logs |
|------------|----------|--------------|
| `[chunk]` | main.py | Chunker output (number of pieces) |
| `[synth]` | main.py | Audio assembly diagnostics |

### Log File Location
```
C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log
```

### How to Read Logs

**1. Check stop reason**:
```powershell
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "stop="
```
Expected: `stop=eos` (normal end), `stop=repeat` (fifth_consecutive triggered)

**2. Check repeat detection**:
```powershell
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.repeat_detected"
```

**3. Check token generation**:
```powershell
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.selected"
```

**4. Check top tokens**:
```powershell
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.top5|t3.mtl_top5"
```

**5. Check adjacent repeats**:
```powershell
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.adjacent_repeat"
```

---

## Test Results

### Test 1: Nano with 30 Numbers

**Input**: "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty."

**Chunking Results** (4 pieces):
- Piece 0 (47 chars): "One. Two. Three. Four. Five. Six. Seven. Eight." - 8 numbers
- Piece 1 (18 chars): "Nine. Ten. Eleven." - 3 numbers
- Piece 2 (76 chars): "Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen." - 8 numbers
- Piece 3 (132 chars): "Twenty. Twenty-one..." - 11 numbers

**Token Generation** (Nano):
| Piece | Tokens | Stop Reason |
|-------|--------|-------------|
| 0 | 146 | eos |
| 1 | 51 | eos |
| 2 | 206 | eos |
| 3 | 301 | eos |

**Key Finding**: Nano completes with `stop=eos` - NOT `stop=repeat`. No fifth_consecutive was triggered.

**Audio Output**:
- Duration: 28.14 seconds at 24000 Hz
- Total samples: 675,360
- All 4 pieces assembled correctly in WAV file

### Test 2: V3 with 30 Numbers

**Token Generation** (V3):
| Piece | Tokens | Stop Reason |
|-------|--------|-------------|
| 0 | 129-136 | repeat |
| 1 | 64-78 | repeat |
| 2 | 75-179 | repeat |
| 3 | 189-310 | repeat |

**Key Finding**: V3 triggers fifth_consecutive detection on all pieces

### Key Observations

1. **Nano does NOT trigger repeat detection** - tokens are diverse
2. **V3 DOES trigger repeat detection** - at position 311 (fifth consecutive)
3. **Tokens are diverse** - top-5 shows different token IDs each position
4. **No single problematic token** - token 4218 appears but not dominantly

---

## WAV File Analysis

### WAV File Structure
```
File: out_06-09-26-22-34-06_tts.wav
- Format: PCM, 1 channel, 16-bit
- Sample Rate: 24000 Hz
- Total Size: 1,350,764 bytes
- Data Size: 1,350,720 bytes
- Duration: 28.14 seconds
- Total Samples: 675,360
```

### Audio Assembly
```
Piece 0: 278,400 bytes = 139,200 samples = 8.7 seconds
Piece 1:  97,920 bytes =  48,960 samples = 3.06 seconds
Piece 2: 395,520 bytes = 197,760 samples = 12.36 seconds
Piece 3: 578,880 bytes = 289,440 samples = 18.09 seconds
Total: 1,350,720 bytes = 675,360 samples = 42.2 seconds @ 16kHz
       (or 28.14 seconds @ 24kHz)
```

**Note**: TTS_RATE is 24000 Hz, not 16000 Hz. WAV duration calculation:
```
DataSize / (channels * sampleRate * bitsPerSample / 8) = Duration
1,350,720 / (1 * 24000 * 2) = 28.14 seconds
```

---

## Areas of Investigation

### 1. Audio Quality Issue (User Reports Repetitions)

**Symptoms**:
- 1-30 numbers ARE played
- But repetitions are heard

**Possible Causes**:
1. **S3Gen overlap mechanism** - pieces may overlap in a way that causes repetition
2. **Audio concatenation issue** - pieces not spliced correctly
3. **Sample rate mismatch** - downstream processing expects 16kHz but gets 24kHz
4. **Player issue** - audio player only playing first piece or looping

### 2. S3Gen Pipeline Investigation

**File**: `chatterbox.cpp/src/s3gen_pipeline.h`

**Key constant**: `kSpeechHistoryTokens = 25`

**What it does**: Maintains last 25 speech tokens for cross-chunk continuity

**Current behavior**:
- After each chunk, `speech_history` is updated with last 25 tokens
- This enables smooth transitions between pieces

### 3. Model Architecture Investigation

**V3 (MTL) specific**:
- Uses CFG (Classifier-Free Guidance) with `cfg_weight=0.3`
- Applies `repeat_penalty` to logits during sampling
- Has fifth_consecutive early stop

**Nano/Turbo specific**:
- No CFG (cfg_weight=0)
- Uses GPT2_BPE tokenizer
- Had NO repeat detection until our changes
- Apply repeat_penalty differently (penalizes ALL past tokens, not just consecutive)

---

## Commands Reference

### Rebuild and Test
```powershell
# Rebuild
python "C:\Users\eb-wjt\Downloads\Trident\tts_v3.py" --install

# Load server (keeps running for multiple tests)
python "C:\Users\eb-wjt\Downloads\Trident\tts_nano.py" --load

# Test with text
python "C:\Users\eb-wjt\Downloads\Trident\tts_nano.py" --text "One. Two. Three..."

# Unload server
python "C:\Users\eb-wjt\Downloads\Trident\tts_nano.py" --unload
```

### Git Commands
```powershell
# Check chatterbox.cpp status
git -C "C:\Users\eb-wjt\Downloads\chatterbox.cpp" status

# Check Trident status
git -C "C:\Users\eb-wjt\Downloads\Trident" status

# Commit and push
git -C "C:\Users\eb-wjt\Downloads\Trident" add .
git -C "C:\Users\eb-wjt\Downloads\Trident" commit -m "message"
git -C "C:\Users\eb-wjt\Downloads\Trident" push origin v3-optimization
```

### Log Analysis
```powershell
# View recent logs
Get-Content "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Tail 100

# Search for patterns
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.repeat_detected"
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "stop="
Select-String -Path "C:\Users\eb-wjt\Downloads\Trident\.runtime-logs\tts.log" -Pattern "t3.selected"
```

---

## Files to Check for Further Investigation

### 1. S3Gen Pipeline
- `chatterbox.cpp/src/s3gen_pipeline.h` - kSpeechHistoryTokens
- `chatterbox.cpp/src/chatterbox_engine.cpp` - `s3gen_synthesize()` call

### 2. Audio Processing
- `chatterbox.cpp/src/s3gen_pipeline.cpp` - `s3gen_synthesize()` implementation
- Check how overlap_ms is calculated

### 3. WAV Header Processing
- `Trident/main.py` - `synthesize()` method
- Check if TTS_RATE constant matches actual sample rate

### 4. Chunking Logic
- `Trident/chunk.py` - how text is split
- Check overlap settings between pieces

---

## Potential Next Steps

### 1. Investigate S3Gen Overlap
The S3Gen has an overlap mechanism for smooth transitions. Check:
- How overlap_ms is calculated
- If overlap is causing duplicate audio
- The `synthesis.overlap_ms` field in logs

### 2. Check Audio Player
- Try playing the WAV file with different players
- Check if player supports 24kHz or needs resampling

### 3. Test with Simpler Text
- Single words to isolate the issue
- Short phrases without numbers
- Compare audio output

### 4. Compare Models
- Test Turbo with same text
- Check if issue is Nano-specific or affects all models

### 5. Add Token Decoding Logging
- Decode token IDs to actual phonemes/text
- Verify tokens represent correct sounds

---

## Facts vs Assumptions

### FACTS (Confirmed)
1. Nano generates diverse tokens (not repeating the same token)
2. Nano completes with `stop=eos` (no fifth_consecutive triggered)
3. V3 triggers fifth_consecutive at position 311
4. All 4 pieces generate audio correctly
5. WAV file contains all 675,360 samples (28.14 seconds)
6. TTS_RATE is 24000 Hz (not 16000 Hz)
7. Nano and Turbo have CFG disabled (cfg_weight=0)
8. V3 has CFG enabled (cfg_weight=0.3)
9. repeat_penalty=1.2 for Nano/Turbo, 1.5 for V3
10. The chunker splits "1-30" into 4 pieces of 8,3,8,11 numbers

### OBSERVATIONS (Need Verification)
1. Audio has repetitions - but WAV file is complete
2. Numbers 1-30 ARE spoken (user confirmed)
3. The scrambled order "9 10 twenty, eleven" was from earlier V3 test
4. Current Nano test produces complete audio

### ASSUMPTIONS (Not Verified)
1. The repetition is in the audio content, not audio assembly
2. S3Gen overlap mechanism might be causing issues
3. The issue might be in how pieces transition to each other
4. Token-to-phoneme decoding might be wrong

---

## Revision History

| Commit | Description |
|--------|-------------|
| `335a94e` | Comprehensive logging for Nano/Turbo root cause analysis |
| `753b7c1` | Diagnostic logging for audio assembly |

---

## Contact Information

- User: @wgabrys88
- Repos: chatterbox.cpp, Trident (both on v3-optimization branch)

---

## Appendix: Sample Log Entries

### Nano Generation Start
```
t3.start: text_len=47 text='One. Two. Three. Four. Five. Six. Seven. Eight....' n_predict=1000 repeat_penalty=1.20 cfg_weight=0.00 temp=0.80 top_k=1000 top_p=0.95
```

### Tokenization
```
t3.tokenized: text_tokens_count=16 first_5=[3198,13,4930,13,7683] last_5=[13,18087,13,13723,13]
```

### Top Tokens
```
t3.top5: pos=0 [(1490,11.673),(1733,11.505),(1517,10.369),(1732,10.247),(1736,10.069)]
```

### Selected Token
```
t3.selected: pos=0 token=1813 temp=0.80
```

### Adjacent Repeat
```
t3.adjacent_repeat: position=284 token=2112
```

### Token Progress
```
t3.token_progress: position=220 token=2483 logits_max=15.9027
```

### Stop Reason
```
t3 epoch=0 piece=0 mono_us=14462890714 | nano  tokens=146 ms=964 stop=eos cfg_weight=0.000000 repeat_penalty=1.200000
```
