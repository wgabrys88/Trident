# Endgame desktop

Machine PE-DMLW, branch `cursor/endgame-desktop-628c`. The card listener was not already bound to 8765. A short `node.py` process was started for the card checks and then stopped.

Caps from `desk.py caps` and from a `node hello` card:

- desktop yes
- telegram-desktop no
- chrome yes (`C:\Program Files\Google\Chrome\Application\chrome.exe`)
- owner no, call no, end no
- cursor yes (`agent` resolves)
- mic yes

`mic yes` comes from `sounddevice`. The capture list includes `Microphone FP`, `Line-In`, `Digital-In`, and Creative "What U Hear" entries, plus VB-Audio cable devices that the check skips. No call was placed, so this seat did not switch a capture device.

Telegram call, `desk.py call`, exit 2: `telegram desktop absent`. The check stops before focus, before a UI Automation scan, and before the owner file. There is no `Telegram.exe` under LocalAppData or AppData.

Chrome ask. No Chrome window was open. `desk.py ask --window ChatGPT --url https://chatgpt.com/ --text "Reply with the single word pebble."` opened Chrome. The ChatGPT window appeared and took focus. For 45 seconds the top window had no Edit, ComboBox, Spinner, or Document control. Locate then required the screenshot-vision server. That binary is not in the clone:

`server binary not found at C:\Users\px-wjt\Downloads\Jarvis\Trident\screenshot-vision\bin\llama-server.exe`

`endgame.job` after that run:

```
pid 5896
lease cli
op ask
stage halt
verdict denied
reason server binary not found at C:\Users\px-wjt\Downloads\Jarvis\Trident\screenshot-vision\bin\llama-server.exe - run: python main.py install
```

Nothing was pasted and no answer was witnessed. The same blockers came back as cards on 8765: `endgame run` returned `err window absent` for an empty body, `telegram run` returned `err telegram desktop absent`, and `call hangup` returned `err no live call`.

Cursor. The agent CLI is installed and the status file was empty. `cursor.py start` was not run. This checkout already has a live Cursor agent, and a start would launch a second one.
