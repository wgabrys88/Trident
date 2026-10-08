# Trident

One Windows process per device. They share a folder bus. `run.py` is the supply. It is not on the bus and it carries no frames. Luna is the only model that decides. Her model string is `config.toml` key `luna.model`, `gpt-5.6-luna-none`. The same string is in `prompt.txt`. It is not in the master prompt.

```mermaid
flowchart LR
  run[run.py supply]
  t10[0x10 timer]
  t11[0x11 telegram]
  t12[0x12 ears]
  t13[0x13 voice]
  t14[0x14 tools]
  t16[0x16 luna]
  t50[0x50 memory]
  t48[0x48 injector]
  obs[observer]
  run --> t10
  run --> t11
  run --> t12
  run --> t13
  run --> t14
  run --> t16
  run --> t50
  t10 --- bus[(wire)]
  t11 --- bus
  t12 --- bus
  t13 --- bus
  t14 --- bus
  t16 --- bus
  t50 --- bus
  t48 --- bus
  obs -.-> bus
```

The supply does not start `0x48` or the observer. The injector is drawn on the bus because it is a device. The observer is dotted because it has no address.

## Provenance

Each mechanism is one of these: NXP UM10204 Rev. 7.0 (1 October 2021), SMBus 3.3.1 (20 October 2024), ISO 11898-1:2015 clause 12.1, or a recorded choice. A recorded choice is where the standard is silent, or where a folder on this computer cannot be a wire. A recorded choice is protocol.

| Mechanism | Source |
| --- | --- |
| Frame line `S`, address, `W` or `R`, `A` or `NA`, data bytes, one `Sr`, `P` | I2C UM10204 §3.1.4, §3.1.5, §3.1.6, §3.1.10. 7-bit addresses only. 10-bit addressing (§3.1.11) is not used. |
| ACK means the byte arrived. The result of a command is a later read. | I2C UM10204 §3.1.6. Recorded choice for the result: the standards do not define a second channel, so the result is the repeated-start read or a later read. |
| Five NACK causes | I2C UM10204 §3.1.6, listed in the Bus section. |
| A target acts only when a controller addresses it | I2C UM10204 §3.1.10. Luna is woken only by a frame addressed to `0x16`. No device sends her a frame on a timer. |
| Reserved addresses `0000 XXX` and `1111 XXX` are not used | I2C UM10204 §3.1.12. |
| Injector address `0x48` (`1001 000b`) | SMBus 3.3.1 §6.2.2.3. Prototype addresses are `1001 0XXb`. They are not for a production part. The injector is not in the supply's start list. |
| Clock stretch budget | SMBus 3.3.1 §4.2.2 `tTIMEOUT` is 25 ms to 35 ms. Recorded choice: the budget is the `busy` limit for that device, because the work is a model, a shell, or a screenshot, not an SMBus command. The supply ends a live hold older than `busy`. |
| No Host Notify, no cancel of an open Luna turn | SMBus 3.3.1 §6.5.9 is the standard way for a target to become a controller and notify the host. It is not implemented. UM10204 has no abort from a second controller. A write to Luna during an open turn is NACK cause 2. The controller retries. The turn is not cancelled. |
| Error counter steps +8 and −1, and bus-off | CAN ISO 11898-1:2015 clause 12.1. The node that detects a transmit error adds 8. A success subtracts 1. Bus-off in the standard is a transmit counter of at least 256. Recorded choices are in the Bus section: one counter, the threshold is the limit `bus_off`, and there is no recessive-bit recovery. |
| Folders and files instead of SDA and SCL | Recorded choice. This computer has no I2C wires. `wire/<aa>/inbox/` is the target. The file `q-<controller>-<seq>` is the frame. The reply is `r-<seq>` in the controller's inbox. |
| The frame has no sender field. The filename carries the controller address. | Recorded choice. UM10204 §3.1.10 sends the target address, not the controller's. The journal column `src` is that same controller address. Luna's turn input ends with that address and then the message, so the device and the journal name the same controller. |
| No global lock. Two targets can be addressed at once. Two frames for one target are taken in filename order. | Recorded choice. UM10204 §3.1.7 and §3.1.8 arbitrate with a wired-AND on SDA. A file has no wired-AND. |
| Sharing errors 5 and 32 retry `share_retries` times from `share_delay`, doubling | Recorded choice. Windows file sharing is not an I2C event. The retry is the whole rule. There is no second recovery. |
| SCL is the file `wire/scl/<aa>` containing the pid and the time. Tools holds it while a job runs. The injector holds it while it masters the requested frame. Luna does not hold it. | Recorded choice for the file. The hold itself is UM10204 §3.1.9. Luna's CLI is off the bus after she ACKs: stretch is optional, and her budget is `busy.luna`, not a clock hold. |
| Only telegram `0x11` and ears `0x12` carry the owner's words | Recorded choice. The standards have no owner. Any controller may address Luna (UM10204 §3.1.10). A frame from any other address is still accepted, and it is not his words. |
| No cap on the conversation in Luna, no delete on memory, no paid-turn cap, no input register in the standards | Recorded choices in the device sections. The standards say nothing about storage, cost, or pointer meaning. UM10204 leaves the data bytes to the device. |

## Bus

A frame is one ASCII line:

```text
S <aa> <W|R> <A|NA> (<hh> <A|NA>)* [Sr <aa> <W|R> <A|NA> (<hh> <A|NA>)*] P
```

`aa` is the 7-bit address in hex. `hh` is one data byte. `A` means that byte arrived. `NA` is NACK. At most one `Sr`, and it names the same address as `S`. A read with data ends with `NA` on the last data byte. An empty read has no data byte. A legal frame names an address in `config.toml` `[address]`.

`i2c.py` is the bus library. Every device links it and imports no other device. Started without `--test` it prints `i2c.py is the wire` and exits 1.

The controller writes the frame to a temp file, fsyncs it, and renames it to `q-<controller>-<seq>`. The target scans its inbox (no directory watcher), takes the first `q-` name in filename order, and writes the reply to the controller inbox as `r-<seq>`.

ACK is only delivery.

NACK, the five causes in UM10204 §3.1.6, as this bus uses them:

| Cause | What the code does |
| --- | --- |
| 1 no receiver | No `alive` file. `request` raises `NACK <aa>` and does not place the frame. The counter does not move. Recorded choice: absence is not a CAN error frame, because the carrier sees the missing file before a frame is written. There is no node to charge. |
| 2 not ready | Address `NA` while `alive` exists and the device is not bus-off. Luna does this while a turn is open. A handler exception also replies with address `NA`. The controller keeps the frame and retries after `frame_timeout` for as long as the target is alive and not bus-off. No retry cap. Not a counter event. Recorded choice: UM10204 does not define a retry count. |
| 3 command rejected | The target raises `Nack`. The reply is `NA` on the first data byte. The controller adds 8 and raises. The target does not add. Recorded choice: a data NACK is a legal I2C reply, not a CAN error frame. The controller is the node that sees it. |
| 4 no more room | Not a separate path. A bad register uses cause 3. |
| 5 end of read | The last read data byte is `NA`. Not a fault. The `NA` that ends a read is not cause 3. |

Each device has one counter in `wire/<aa>/err`, not a separate transmit counter and receive counter. Recorded choice: each process is one node, and this carrier has no bit-level error frames. The steps are the CAN steps and they are not limits: a detected fault adds 8, a success subtracts 1, floored at 0. The target subtracts 1 when it ACKs. The controller subtracts 1 when it accepts the reply. A handler exception adds 8 on the target. A controller timeout adds 8 on the controller and writes a journal line `timeout`. The silent target is not charged. Recorded choice, against charging the device that stayed silent: ISO 11898-1 charges the node that detects the error, and one node does not write another node's counter. At `bus_off` the device writes `busoff`, prints `<aa> bus-off`, and a later inbox frame gets address `NA`. The controller's `request` does not place a frame to a bus-off target; it raises `NACK <aa>` and does not add. The threshold is the limit `bus_off`, not 256. Recorded choice: there is no bit time to integrate. There is no recovery of 128 times 11 recessive bits. Recorded choice: a folder has no bit times. The supply starts the process again, and `up` zeros the counter. CAN allows the host to rejoin a bus-off node. The supply is that host.

`frame_timeout` is how long a controller waits for `r-<seq>` when SCL is free. If `wire/scl/<aa>` exists, the wait extends to that device's `busy` time. The file contains the pid and the time. `run.py` deletes the file when that process is dead. If the process is alive and the hold is older than `busy`, `run.py` terminates it.

One journal line per handled frame, and one from the controller on timeout, in `RUN_<stamp>/bus.log`:

```text
<time> <src> <dst> <note> <ms> <frame>
```

The handled line's frame is the reply. The timeout line's frame is the request. `src` is the controller address. `dst` is the target.

Large data does not ride the bus. A frame carries a path. The run folder holds `bus.log`, `turn-<n>.txt`, `pcm/<n>.pcm`, and `wav/<n>.wav`. A `turn-<n>.txt` file is written only when the CLI returns zero. A timeout or a non-zero exit does not write one.

## Address map

| Address | Process | Role |
| --- | --- | --- |
| 0x10 | timer.py | Boot dial and redial |
| 0x11 | telegram.py | Owner's Telegram user, chat and call. Owner's line. |
| 0x12 | ears.py | Nemotron on a PCM file. Owner's line. |
| 0x13 | voice.py | Chatterbox nano, turbo s3gen codec |
| 0x14 | tools.py | Screenshot, shell, vision, pointer and keys |
| 0x16 | luna.py | One Cursor CLI turn |
| 0x48 | injector.py | Controller-only test device. Not started by the supply. |
| 0x50 | memory.py | SQLite records |

`run.py` starts timer, telegram, ears, voice, tools, luna, memory, in that order, passing the `RUN_<stamp>` path. It deletes `wire/` at start. It does not delete `life/` or `life/memory.sqlite`. It creates `session/<stamp>/` and `RUN_<stamp>/`.

## Registers

Bytes after the address. A bare read is `S <aa> R`. A command result is the same write plus `Sr <aa> R`.

### 0x10 timer

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `02` | W | He did not answer. ACK. If no wait is already set, redials are under `redial_cap`, and Luna is up, arm one wait of `retry_seconds`. A second `02` does not move that deadline. |
| `03` | W | Cancel the wait and zero the redial count. |
| other | W | Data NACK. |
| read | R | `00` idle, `01` a wait is armed. |

The wait does not hold SCL.

### 0x11 telegram

The session is the owner's Telegram Desktop `tdata`. The account must be one user who is not the owner and not a bot. Incoming calls from the owner are answered. Chat is his private messages only, and only ids newer than the last one seen at start. This device is on the owner's line: the frames it masters to Luna are his words.

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` | W | Dial. ACK and start the call only when state is down. Otherwise data NACK. The ring wait is `ring_seconds`. |
| `02` | W | Hang up. |
| `10` then UTF-8 | W | Send that chat text, in slices of `chat_slice`. If the call engine is up, also master voice `01` plus the same text. |
| `20` then a path | W | Queue that wav. `pump` plays it only while the call engine is up, as `play_rate` Hz PCM in frames of `play_frame` bytes. |
| other | W | Data NACK. |
| read | R | `00` down, `01` dialing, `02` up. |

Microphone audio is `pcm_rate` Hz PCM inside the process, in frames of `frame_ms` ms. VAD uses `rms_threshold`, `silence_ms`, `padding_ms`, and `utterance_seconds`. Each finished utterance is `RUN_<stamp>/pcm/<n>.pcm`, then ears register `01` plus that path. No answer (discarded call or the ring wait) hangs up and masters timer `02`. The connect wait after accept, and the wait for the confirmed call, are `connect_seconds`.

### 0x12 ears

This device is on the owner's line: a non-empty transcript it masters to Luna is his words.

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` then a PCM path | W | ACK, then off the bus run VAD on the file and Nemotron at `pcm_rate`. Non-empty text is mastered to Luna as raw UTF-8, with no register byte. |
| other | W | Data NACK. |

Load of the DLL must finish within `busy.ears` or the process exits. The VAD frame is `frame_ms` at `pcm_rate`, 16-bit mono.

### 0x13 voice

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` then UTF-8 | W | ACK, then synthesize off the bus. The wav is `RUN_<stamp>/wav/<n>.wav`. Then master telegram `20` plus that path. |
| other | W | Data NACK. |

The server must answer `/health` within `busy.voice`. The health request uses `health_timeout`. Speech is `POST` to `<url>/v1/audio/speech` with `http_timeout`. Stderr kept for the exit line is `stderr_keep` bytes. The poll between health checks is `health_poll`.

### 0x14 tools

Holds SCL until the job returns. A following `Sr` read is the result bytes.

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` then a png path | W | Screenshot of the virtual screen into that path. Result is the path text. |
| `02` then a command | W | `cmd` shell. Result is stdout plus stderr, and `exit <code>` when the code is not 0. |
| `03` then an image path | W | Local vision model. The only prompt is "Describe this image." Temperature is `vision.temperature`. The token cap is `vision.max_tokens`. Result is that text. Not a mind. |
| `04` then UTF-8 | W | Pointer or keys on the same virtual screen. `click y x`, `type text`, or `key name`. `y` and `x` are integers from 0 through `grid`, y down and x right. The result of a click is the screen pixels `<px> <py>`, horizontal then vertical. The result of type or key is the text that was sent. A bad verb, a coordinate outside the grid, or an unknown key is data NACK. |
| other | W | Data NACK. |

The vision server must answer `/health` within `busy.tools`. The health request uses `health_timeout`. The request is `POST` to `<url>/v1/chat/completions` with `http_timeout`. Stderr kept for the exit line is `stderr_keep` bytes. The poll between health checks is `health_poll`.

The virtual screen is the Windows rectangle `SM_XVIRTUALSCREEN`, `SM_YVIRTUALSCREEN`, `SM_CXVIRTUALSCREEN`, `SM_CYVIRTUALSCREEN`. The click call uses `MOUSEEVENTF_ABSOLUTE` and `MOUSEEVENTF_VIRTUALDESK`, whose absolute range is 0 to 65535. That range is the Win32 call, not a limit. Named keys are enter, tab, esc, space, backspace, up, down, left, right, delete, home, and end. One ASCII letter or digit is that key.

`prompt.txt` tells Luna the same grid, 0 to 1000, which is `limits.grid`.

### 0x16 luna

| Bytes | Dir | Meaning |
| --- | --- | --- |
| UTF-8, no register | W | The incoming message. ACK at once, then one CLI run off the bus. A write while that turn is open is address NACK, cause 2. The read below still works. |
| read, no write data | R | `00` idle, `01` the turn is open. |

Nothing addresses her unless a device masters that frame. No device sends her a periodic frame.

Stdin is `prompt.txt`, then the address list, then the conversation kept in this process, then one line with the controller address, then the message. The address is the `q-` filename's controller, because the frame has no sender field. Words from `0x11` and `0x12` are the owner's. Words from any other address are not. A write from `0x11` or `0x12` zeros the self-turn counter. A write from her own address increments it. A write from any other address does not zero it and still starts a turn.

The CLI is the newest `%LOCALAPPDATA%\cursor-agent\versions\*` folder that contains `node.exe` and `index.js`, run as `-p --model <luna.model> --output-format text --trust --workspace <RUN dir> --exclude-tools <tool list>`. No `--force`, no stream-json, no API key. The real CLI was not run; the tool list was copied from that install's protocol names.

Stdout: a legal frame is mastered, one at a time. Any other text is one telegram `10` write. Empty text sends nothing. A read's payload, and a write she addresses to `16`, are placed in her inbox after the turn and count as self-turns. Past `self_turn_cap` the frame is ACKed and no CLI starts.

If a frame she masters is NACKed or times out, that turn does not end as `Luna failed`. The exception text is queued as her next turn, and the later lines of that output are still mastered or said. UM10204 §3.1.6: the controller sees the NACK and decides. This conflicts with keeping one telegram line for every failed turn. That rule stays for a failed CLI, where she produced no frames. It does not stay for a bus NACK.

If the CLI runs longer than `busy.luna`, it is killed and telegram is sent one `10` write, the text `Luna timed out.` A non-zero CLI exit sends one `10` write, `Luna failed: <tail>`. Both add 8. The turn record for a zero exit is `RUN_<stamp>/turn-<n>.txt`.

The conversation in this process has no cap and no compaction. It dies when the process exits. A new owner-line frame can start another 1 + `self_turn_cap` CLI runs, each at most `busy.luna`. There is no per-run paid-turn cap. CAN bus-off counts faults, not successful turns. Recorded choice: the standards do not bound a successful transaction.

### 0x48 injector

Controller only. Not in the supply's start list. Start it by hand against a run folder:

```text
artifacts\python\Scripts\python.exe injector.py RUN_<stamp>
```

Its frames are `q-48-<seq>`. The journal `src` is `48`. They are not the owner's words and they are not live proof.

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` then a legal frame as UTF-8 | W | ACK, hold SCL, master that frame, store the reply. An `Sr` read returns the reply text. |
| other | W | Data NACK. |
| read | R | The last reply text, or no data bytes when none has been mastered. |

An illegal frame is data NACK. `busy.injector` equals `busy.tools` because tools is the device that holds SCL for a job, and the injector's caller waits on the injector's SCL for that whole inner transfer.

### 0x50 memory

SQLite file `life/memory.sqlite`, table `rec(id, body)`. No delete. No size limit. The file survives a start, because the supply deletes `wire/` only. Recorded choice: the standards say nothing about storage.

| Bytes | Dir | Meaning |
| --- | --- | --- |
| `01` then bytes | W | Append a row. `Sr` reads those bytes back. |
| `02` then decimal id | W | `Sr` reads that row. Missing id is data NACK. |
| `03` | W | `Sr` reads the latest row. |
| other | W | Data NACK. |
| read | R | Latest row, or no data bytes when the table is empty. |

## Observer

`observer.py` has no address, no `alive` file, and no inbox. It is not a device and the supply does not start it. It reads `RUN_<stamp>/bus.log` and the files under `wire/`. It prints a journal line once, and it prints the wire files when their text changes. It never writes, never renames, and never acknowledges. A sharing collision uses the bus read retry and nothing else.

```text
artifacts\python\Scripts\python.exe observer.py RUN_<stamp>
```

## Sequences

Boot dial. This is the timer's frame, not Luna's decision. The timer waits until telegram has `alive` and Luna is up (`alive`, no `busoff`, no `down`). Then once:

```text
S 11 W A 01 A P
```

Nothing else is mastered because she woke. No device masters a frame to `0x16` on a timer.

No answer. Telegram:

```text
S 10 W A 02 A P
```

Timer ACKs and waits `retry_seconds` without SCL. It then masters `01` again. That can happen `redial_cap` times. A `02` during the wait does not stack. After the cap, `02` is ACKed and ignored. `03` clears the wait and the count. If Luna is down, the timer does not dial and does not arm a wait.

Answered call. After the call reaches up, each utterance is:

```text
S 12 W A 01 A <pcm path> A P
S 16 W A <utf-8 text> A P
```

Luna's words, if any:

```text
S 11 W A 10 A <utf-8> A P
S 13 W A 01 A <utf-8> A P
S 11 W A 20 A <wav path> A P
```

Chat from him, call or not, mastered by telegram:

```text
S 16 W A <utf-8> A P
```

Luna's own next turn, after the turn that queued it, and only while under `self_turn_cap`:

```text
S 16 W A <bytes> A P
```

A tools result she asked for with `Sr` is queued the same way, as a write to `16`. A NACK or a timeout of a frame she mastered is queued the same way, as the exception text.

## Limits

Every one of these is a key in `config.toml`. The CAN steps +8 and −1 are not limits. The Win32 absolute range 0 to 65535 is not a limit.

| Key | Value | Effect |
| --- | --- | --- |
| bus.frame_timeout | 2 s | Wait for a reply when SCL is free. Also the cause-2 retry wait. |
| bus.poll | 0.05 s | Inbox scan, and the supply's scan |
| bus.bus_off | 32 | Counter value that takes a device off |
| bus.backoff | 1 s | First restart delay, then double |
| bus.backoff_cap | 30 s | Cap on that delay |
| bus.retry_seconds | 120 | Redial wait |
| bus.redial_cap | 3 | Redials after the boot dial |
| bus.self_turn_cap | 4 | Self-addressed Luna turns in a row |
| bus.luna_start_cap | 3 | Fast Luna exits before "Luna cannot start" |
| bus.stable_seconds | 30 | A run this long clears the crash count |
| bus.share_retries | 5 | Sharing-error attempts, errors 5 and 32 |
| bus.share_delay | 0.05 s | First sharing delay, then double |
| busy.timer | 2 s | SCL budget |
| busy.telegram | 8 s | SCL budget |
| busy.ears | 30 s | Model load budget |
| busy.voice | 60 s | Server start budget |
| busy.tools | 90 s | Server start budget and SCL while a job runs |
| busy.luna | 150 s | CLI budget, off the bus |
| busy.injector | 90 s | SCL budget while the injector masters one frame |
| busy.memory | 2 s | SCL budget |
| limits.ring_seconds | 90 | Outgoing ring wait |
| limits.connect_seconds | 30 | Call connect wait |
| limits.chat_slice | 4000 | Characters per Telegram message |
| limits.play_frame | 960 | Bytes per played PCM frame |
| limits.pcm_rate | 16000 | Microphone and ear sample rate |
| limits.play_rate | 48000 | Speaker sample rate |
| limits.frame_ms | 20 | VAD frame length |
| limits.health_timeout | 2 s | One health request |
| limits.http_timeout | 600 s | One speech or vision request |
| limits.health_poll | 0.2 s | Pause between health checks |
| limits.stderr_keep | 4000 | Stderr bytes kept for the exit line |
| limits.install_timeout | 3600 s | One download |
| limits.grid | 1000 | Pointer grid, inclusive |
| ears.rms_threshold | 0.012 | VAD |
| ears.silence_ms | 700 | VAD endpoint |
| ears.padding_ms | 120 | VAD pre-roll |
| ears.utterance_seconds | 30 | Forced endpoint |
| vision.temperature | 0.2 | Vision request |
| vision.max_tokens | 400 | Vision reply cap |
| selftest.frame_timeout | 0.4 s | Self-test stand-in |
| selftest.poll | 0.01 s | Self-test stand-in |
| selftest.busy | 2 s | Self-test stand-in for every device |

## Errors

A device that raises prints `Type: message` and exits 1. `run.py` restarts it after the backoff. A run longer than `stable_seconds` zeros that device's crash count and backoff. If Luna exits `luna_start_cap` times without lasting that long, `run.py` prints `Luna cannot start`, writes `wire/16/down`, terminates the others, and exits 1. It does not start her again.

A handler exception NACKs the address, adds 8 on that device, and leaves the process up. While that device is not bus-off, the controller retries the address NACK as cause 2. Cause 3 adds 8 on the controller and raises. A timeout adds 8 on the controller. Cause 1 and cause 2 and cause 5 do not add.

## Self-tests

`--test` is the only flag. There is no other test hook, mock, or flag. The tests do not start Telegram, the Cursor CLI, Chatterbox, Nemotron, the vision server, or a browser. They do not move the pointer. `i2c.test_cfg` reads `config.toml` and replaces `frame_timeout`, `poll`, and every `busy` value with the `selftest` keys.

```text
py -3.11 i2c.py --test
py -3.11 memory.py --test
py -3.11 timer.py --test
py -3.11 luna.py --test
py -3.11 run.py --test
py -3.11 ears.py --test
py -3.11 voice.py --test
py -3.11 tools.py --test
py -3.11 telegram.py --test
py -3.11 injector.py --test
py -3.11 observer.py --test
```

`i2c.py` checks the frame grammar, accepts `0x48`, rejects `0x08` and `0x99`, checks a repeated-start read, SCL during a short hold, cause 2, a timeout that charges the controller (`12 bus-off`), and a sharing-violation rename. `memory.py` writes bytes `68 69` and reads them back. `timer.py` checks one boot dial, a `02` that does not stack, the redial cap, `03`, and no SCL during the wait. `luna.py` uses a stand-in process: no SCL while it runs, cause 2 on a second write, prose to telegram `10`, the controller address `11` in the turn file, the owner line `{0x11, 0x12}`, the self-turn cap, a write from `0x13` that does not reset that cap, a NACK that still delivers the later prose and comes back as the next turn, and the timeout sentence. `run.py` terminates a live SCL hold, clears a dead one's SCL, checks `Luna cannot start`, and checks that the injector and the observer are not in the start list. `ears.py` checks a VAD cut. `voice.py` checks resample between `pcm_rate` and `play_rate`. `tools.py` checks a shell register, the grid map, and a data NACK for a bad `04` body. `telegram.py` checks dial, chat, no-answer, and play frames. `injector.py` checks that the frame it masters arrives with source `0x48`. `observer.py` checks that a read of the journal and a `q-` file changes no byte and creates no `alive` file.

## Install and run

64-bit Python 3.11 on Windows, a Cursor CLI login, and Telegram Desktop tdata.

```text
py -3.11 install.py
artifacts\python\Scripts\python.exe run.py
```

`install.py` makes `artifacts\python`, installs `requirements.txt`, and downloads the ear, voice, and vision files named in `config.toml`. A download uses `install_timeout`. A full install was run once in a throwaway folder and then deleted. It was not run again for this tree.

## Unproven live

Not run on this tree: Trident itself, a real Cursor CLI turn, a Telegram call or chat, Chatterbox, Nemotron, the vision model, and a browser. The `--exclude-tools` argument was not executed. The self-tests above were run. Live proof was not run.

## Master prompt

This is the master prompt for Trident. Every AI agent working on Trident gets it pasted in verbatim, with only the TASK fields filled. Those agents have no memory of any earlier chat, so this text and the tree are all they know. It holds the owner's timeless rules for what Trident is and how it may be changed. The datasheet above it holds today's facts: the devices, the addresses, the registers, the sequences, the model, and the limits. The prompt lives in this README so it is not lost, even if every other copy disappears.

Version 20. Date 2026-10-08.

Changes from v19:

1. The PROTOCOL section is new, and it is the first section of the prompt. NXP UM10204, the SMBus mechanisms this datasheet borrows, and the CAN mechanisms this datasheet borrows outrank the prompt, any earlier owner preference, any habit of the tree, the owner, and a datasheet line that contradicts them. v19 did not say this. A no-memory agent could follow a conflicting sentence in the prompt. Where the standards are silent, the choice is recorded in this datasheet, and that record is protocol. Nobody may customize the protocol.
2. The fault sentence in THE BUS is rewritten. v19 said every fault is charged to exactly one device. ISO 11898-1:2015 clause 12.1 charges the node that detects the fault. UM10204 §3.1.6 condition 1 is an address with no receiver, which is not a node and is not charged. The old sentence conflicted with those standards.
3. The sentence that a famous protocol means a fault belongs to one device is rewritten. It now says an agent can read the bus without memory of Trident, and it points the charging rule at the datasheet. The old wording contradicted the CAN rule in change 2.

```text
You are an agent with no memory of any earlier chat. This text is the master prompt for Trident. It is the same text in the past and in the future. It does not name today's devices, addresses, registers, sequences, models, or limits, because those live in the datasheet and change there. It names what the project is, how it behaves, and the rules for changing it. Read it from the top to the bottom. Then read README.md, the datasheet, in full. Then read the tracked tree. Then do only the TASK at the end.

PROTOCOL

The published protocols this bus is built on outrank every other rule. Those protocols are NXP UM10204 I2C, the SMBus mechanisms the datasheet borrows, and the CAN mechanisms the datasheet borrows. They outrank this prompt, any earlier owner preference, any habit of the tree, and the owner. They outrank a datasheet line that contradicts them: correct that line and record the standard's section. When any rule conflicts with those protocols, follow the protocol, record the conflict and the standard's section in the datasheet, and drop or rewrite the conflicting rule. Nobody may customize the protocol. Where the standards are silent, record the choice and its reason in the datasheet. A recorded choice is protocol.

WHAT THIS IS

Trident is one local organism on the owner's Windows computer. She is built the way electronics are built: independent devices on one bus. README.md is the bus datasheet. The frame grammar, acknowledgements, timeouts, error counting, how frames travel on the wire, the address map, every register, every sequence, and every limit are defined there and nowhere else.

The protocol is sacred. It follows real published standards: NXP UM10204 I2C for frames and addressing, with pieces borrowed from SMBus and CAN. The datasheet labels each mechanism with the standard it comes from. Where the standards are silent, or where the wire on this computer has to differ (for example, folders and files standing in for wires), the datasheet records that choice and its reason. A recorded choice is protocol, not a deviation to fix. Nothing else may be invented: no private envelope and no field the frame grammar does not define. A famous, documented protocol means any agent can read the bus without memory of Trident. Which node is charged for a fault is the datasheet's CAN rule.

Luna is the only brain. She is reached through the owner's own Cursor login on this computer, the same way he uses Cursor himself, using the model the datasheet names. Every model call she makes draws only from his Cursor subscription: no API key, no bring-your-own key, no other paid pool. Running her turns as Cursor Cloud Agents through a key is one possible future route. Only the owner opens it. The tree carries one route at a time, never both, and never a switch between them.

She is text in and text out. Each turn starts from a frame addressed to her. Her output is either frames for the bus or her words to the owner, as the datasheet defines. Nothing wakes her unless a device addresses her. Local models on devices, such as the ear, the voice, and vision, are tools, not minds. No code branch makes a decision she should make from meaning, and no other model takes her place.

The owner reaches her only through his Telegram user account. She is never a bot, and no second bot stands beside her. The computer microphone and the computer speakers are not hers. She hears and speaks on the Telegram line. One ear and one voice, both local, with no switch between variants. Only the devices the datasheet names as the owner's line carry his words.

She works on the computer the way a person would, through general device registers, never a register made for one application. She looks at real pixels before she acts on a place, then reads the result back through the bus before she claims it. She does not guess a place. The owner does not remote in and does not move the pointer for her.

Every start is a fresh life. Nothing from an earlier run returns as a task unless she reads it from the memory device and decides on it.

THE BUS

Each device is its own process at its own address. It links only the bus library and never imports another device. A new capability is a new device or a new register, written into the datasheet first.

The supply starts the devices, restarts them by the datasheet's rules, and clears a stuck clock. It is not on the bus and carries no frames.

ACK means delivery only. A result is a later read. How long a device may hold the clock is defined in the datasheet.

Error counting and bus-off follow the datasheet. The CAN fault-confinement rule the datasheet borrows charges the node that detects the fault. An address with no device is not a node and is not charged.

Large data, such as audio and images, never rides the bus. A frame carries a path to it.

The bus journal is the record of every transaction and every timeout. A claim about behaviour cites journal lines or a self-test. A live claim cites the journal of a real run.

Every limit is a number in the tree's configuration and a row in the datasheet's Limits table. A timeout, wait, cap, or size written as a constant in code is a defect. This prompt names none of them.

Retries, restarts, and timeouts exist only where the datasheet defines them. Those are protocol. Any other retry or recovery is forbidden defensive code.

TESTS, INJECTION, AND OBSERVATION

A device is tested through frames: frames placed in its inbox, replies read back, and every other device absent or replaced by a stand-in at its address. Each device carries one self-test entry, the only flag the datasheet allows, and the datasheet says what each self-test proves. A self-test proves the device keeps the protocol. It does not prove the organism behaves right.

Whole-system scenes are rehearsed by a test device that only masters frames, the way any controller does. It is a device like any other, so its address and rules live in the datasheet. Its frames are known by their source address, never count as the owner's words, and never count as live proof. Device code has no other test hooks, mocks, or flags. If a behaviour cannot be triggered by a frame, the datasheet is missing a register.

Observing the bus means reading the journal and the wire. An observer has no address, never writes, and never acknowledges, the way a logic analyzer watches a real bus.

HOW YOU WORK

If you are run by the Luna device, you are Luna. Answer as the prompt text the tree loads tells you. Never build or commit. If you can launch a coding agent on this repository but should not write its code, launch one writer and stop. That writer's task carries everything known at once: every open ask from the owner and every gap the last handoff left. Nothing is held back for a later wave. If you can read and change the checkout, you are the writer, or the reviewer only when the TASK says review. Do not ask which seat you are.

Read the datasheet, the tracked source the TASK touches, and the text the organism actually loads. The filenames are whatever the tree uses now. Do not rebuild an old layout from memory of another session.

Read the latest commit message and its tag annotation in full. They are the handoff the last session left. Follow what is still true in the files. A device name, a path, a branch name, or a sentence about one PC in that message is memory of a machine, not a law. Rediscover the machine from the machine and from the files.

Do the TASK. If the Goal is empty, briefly state the tip of the tree against this text and the datasheet, from the files, then stop. Do not invent work. A resubmit with the same empty Goal is still empty.

This prompt is submitted again after a turn. A resubmit is the same assignment, not a new one. Continue until the TASK is shown, or you are blocked on the owner.

Change only what the TASK asks. Leave the rest of the tree as you found it.

SCENES

Start. One command, the command the datasheet documents, brings every device up. What happens at boot, including any call placed, is exactly what the datasheet's sequences say and nothing more. A failure is one error in the window that started her. A new task arrives only as the owner's private Telegram message or his speech on a call. A file dropped somewhere else on the drive is not a task.

The call. He calls her, or a call is placed as the datasheet allows. He speaks in ordinary words, and she hears those words as his. She looks before she acts on a place, then looks again. If the screen did not change as she meant, she tries another way. She does not reuse another application's numbers as this screen's grid.

The chat. With no call up, he can still write, and she can still answer in the chat. She does not dial merely to deliver one sentence.

Hangup. The call ends. She stays. The devices stay loaded. Either side may place the next call without restarting the organism.

Proof. On an empty machine, with no chat history and no leftover process, clone the tree, install what the tree's installer installs, start, and live one phone scene that starts from the owner's own words: a real Telegram user call, no bot in the call, and the journal of that run. If you did not live it, say which part you did not run. Do not report a start, an install, or a send unless the output shows it. If this checkout cannot place or take a real Telegram user call on the owner's Windows machine, do not run the live phone scene. Say that live proof is blocked and what you could not reach. A rehearsed scene is never proof.

Judge the whole organism, not one part. Prove a change by living a real task scene from start to end, such as a game played on a real website, and judge what the owner sees and hears. One part may look wrong while the next part corrects it; that is the scene working. A self-test passing is not proof.

RULES OF THE CODE

The datasheet is the law. Change the README section first, then the code to match, in the same commit. Code that does something the datasheet does not say is a defect, and so is a datasheet line the code does not do.

The protocol is the only size rule. The code that joins a device to the bus matches the datasheet exactly, line for line. Everything else in a device is as little code as its datasheet section needs. When a datasheet line is removed, its code goes in the same commit. When one way of doing a thing replaces another, the old code is deleted, not kept beside the new one. The owner has approved any change of architecture that makes Trident leaner: delete, merge, split, or rename files. Every commit message carries a table of line counts per file.

No defensive coding. No fallback, no second path, no silent recovery, no sandbox, no optional wording that hides a missing piece, no duplicate, no dead code, and no comment that only restates the line. Do not add a document the TASK did not ask for; the datasheet is the one document. A device with a missing model, login, session, or required argument raises one error and exits; the supply's datasheet rules decide what follows.

Read configuration from the tree. Do not create a second configuration channel, and do not read product settings from the environment. No key or credential ever enters the tree.

Logging stays small. A run folder holds the journal and the files the datasheet names, nothing more. Nothing reaches the owner's phone that the datasheet does not define.

RULES OF THE WORK

One checkout has one writer. Trident does not run live while a writer works the same checkout. Do not commit and do not push during a live proof.

Make a new commit only when the TASK says to commit. When it does, leave one annotated tag on that commit as the handoff the next agent will read. Never amend, never rebase, and never force-push. Push every commit and its tag at once, to a remote the repository already has. Do not invent a host. Leave the integration branch where it is unless the TASK tells you to move it.

Try an approach twice. If the same command fails twice, stop repeating it. Leave a healthy system, record what happened, and change the approach.

When you report a review, merge items that share one cause, and explain each cause in a few short sentences.

Near the end of your context, finish the phase cleanly. Do not start a proof you cannot finish. When the TASK told you to commit, the latest commit message is what the next agent with no chat will read. That message states what is now true, and what was not run.

TASK

Goal:

Acceptance:

Constraints:

Execution:
```
