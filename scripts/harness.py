# Trident demo harness. Run from repo root: .venv\Scripts\python.exe scripts\harness.py
# Fixtures live under .install/harness. Each demo_* function matches a manual scenario.

import ctypes
import re
import signal
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".install" / "harness"
CFG = ROOT / "trident.txt"
TRACE = None

AUDIO_ID = "1272-128104-0004"
IMAGE_NAME = "pc_6f79b56c-2b0f-471d-9f9d-93932c69a0ce.png"

PROMPT_THINK = (
    "<|turn>system\n<|think|>Reply with digits only after the thought.<turn|>\n"
    "<|turn>user\nWhat is 17 plus 4?<turn|>\n<|turn>model\n"
)
PROMPT_TOOL = (
    "<|turn>system\n<|think|>You are a helpful assistant."
    "<|tool>declaration:add{a:<|\"|>number<|\"|>,b:<|\"|>number<|\"|>}<tool|><turn|>\n"
    "<|turn>user\nAdd 17 and 4.<turn|>\n<|turn>model\n"
)
PROMPT_IMAGE = (
    "<|turn>user\nWhat application is this screen, and which controls can be clicked?<turn|>\n"
    "<|turn>model\n"
)

SAMPLE_PS = r'''
$os = Get-CimInstance Win32_OperatingSystem
$total = [int]($os.TotalVisibleMemorySize / 1024)
while ($true) {
  $c = Get-Counter -Counter '\Processor(_Total)\% Processor Time','\Memory\Available MBytes','\GPU Engine(*engtype_3D)\Utilization Percentage','\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage'
  $cpu = 0; $avail = 0; $gpu = 0; $ded = 0; $shr = 0
  foreach ($s in $c.CounterSamples) {
    $p = $s.Path
    if ($p -match 'processor') { $cpu = $s.CookedValue }
    elseif ($p -match 'available') { $avail = $s.CookedValue }
    elseif ($p -match 'engtype_3d') { $gpu += $s.CookedValue }
    elseif ($p -match 'dedicated') { $ded += $s.CookedValue }
    elseif ($p -match 'shared') { $shr += $s.CookedValue }
  }
  '{0:0.0},{1:0.0},{2},{3:0.0},{4:0.0},{5:0.0}' -f $cpu, ($total - $avail), $total, $gpu, ($ded/1MB), ($shr/1MB)
}
'''

MIC_PS = r'''
param([Parameter(Mandatory=$true)][string]$Mode, [string]$Id)
$src = @"
using System;
using System.Runtime.InteropServices;
public class MicSwitch {
    [Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig {
        int GetMixFormat(string a, IntPtr b);
        int GetDeviceFormat(string a, int b, IntPtr c);
        int ResetDeviceFormat(string a);
        int SetDeviceFormat(string a, IntPtr b, IntPtr c);
        int GetProcessingPeriod(string a, int b, IntPtr c, IntPtr d);
        int SetProcessingPeriod(string a, IntPtr b);
        int GetShareMode(string a, IntPtr b);
        int SetShareMode(string a, IntPtr b);
        int GetPropertyValue(string a, int b, IntPtr c, IntPtr d);
        int SetPropertyValue(string a, int b, IntPtr c, IntPtr d);
        int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
        int SetEndpointVisibility(string a, int b);
    }
    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    class PolicyConfigClient {}
    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, out IntPtr ppInterface);
        int OpenPropertyStore(int stgmAccess, out IntPtr pProperties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
        int GetState(out int pdwState);
    }
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IntPtr ppDevices);
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
        int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string pwstrId, out IMMDevice ppDevice);
        int RegisterEndpointNotificationCallback(IntPtr pClient);
        int UnregisterEndpointNotificationCallback(IntPtr pClient);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorComObject {}
    public static string Current() {
        var e = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice dev;
        int hr = e.GetDefaultAudioEndpoint(1, 0, out dev);
        if (hr != 0) throw new Exception("get " + hr);
        string id;
        dev.GetId(out id);
        return id;
    }
    public static void Set(string id) {
        var policy = (IPolicyConfig)(new PolicyConfigClient());
        foreach (int role in new int[] {0, 1, 2}) {
            int hr = policy.SetDefaultEndpoint(id, role);
            if (hr != 0) throw new Exception("set " + hr);
        }
    }
}
"@
Add-Type -TypeDefinition $src
if ($Mode -eq "get") { [MicSwitch]::Current() }
else { [MicSwitch]::Set($Id); [MicSwitch]::Current() }
'''


def say(text):
    print(text, flush=True)


class Trace:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.scenario = "setup"
        self.lock = threading.Lock()
        self.alive = True
        (folder / "run.txt").write_text(
            "Demo run. Each subfolder is one scenario with settings.txt, output.txt, and optional artifacts.\n",
            encoding="utf-8",
        )
        self.csv = open(folder / "usage.csv", "w", encoding="utf-8", newline="")
        self.csv.write(
            "time,scenario,cpu_pct,ram_used_mib,ram_total_mib,gpu_3d_pct,vram_dedicated_mib,vram_shared_mib\n"
        )
        self.csv.flush()
        script = folder / "sample.ps1"
        script.write_text(SAMPLE_PS, encoding="utf-8")
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-File", str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line or not line[0].isdigit():
                continue
            with self.lock:
                name = self.scenario
            stamp = datetime.now().isoformat(timespec="seconds")
            self.csv.write(stamp + "," + name + "," + line + "\n")
            self.csv.flush()
            if not self.alive:
                break

    def begin(self, name, why):
        with self.lock:
            self.scenario = name
        path = self.folder / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "why.txt").write_text(why.strip() + "\n", encoding="utf-8")
        (path / "settings.txt").write_bytes(CFG.read_bytes())
        return path

    def output(self, name, text):
        (self.folder / name / "output.txt").write_text(text, encoding="utf-8")

    def close(self):
        self.alive = False
        if self.proc.poll() is None:
            self.proc.kill()
        self.thread.join()
        self.csv.close()


def ps(mode, device=""):
    CACHE.mkdir(parents=True, exist_ok=True)
    script = CACHE / "mic.ps1"
    script.write_text(MIC_PS, encoding="utf-8")
    done = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), "-Mode", mode, "-Id", device],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(done.stderr.strip() or "mic switch failed")
    return done.stdout.strip()


def cable_capture_id():
    done = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$base='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Capture';"
            "Get-ChildItem $base | ForEach-Object {"
            "$p=Get-ItemProperty (Join-Path $_.PSPath 'Properties');"
            "$n=$p.'{a45c254e-df1c-4efd-8020-67d146a850e0},2';"
            "if ($n -is [byte[]]) { $n=[Text.Encoding]::Unicode.GetString($n) };"
            "$n=($n -replace \"`0\", '').Trim();"
            "$state=(Get-ItemProperty $_.PSPath).DeviceState;"
            "if ($state -eq 1 -and $n -eq 'CABLE Output') { '{0.0.1.00000000}.' + $_.PSChildName }",
            "}",
        ],
        capture_output=True,
        text=True,
    )
    found = [line.strip() for line in done.stdout.splitlines() if line.strip().startswith("{0.0.1.")]
    if not found:
        raise SystemExit("CABLE Output capture device not found")
    return found[0]


def cable_play_device():
    for index, dev in enumerate(sd.query_devices()):
        name = dev["name"]
        if "CABLE Input" in name and int(dev["max_output_channels"]) == 2 and "16ch" not in name:
            return index
    raise SystemExit("CABLE Input playback device not found")


def play(path):
    audio, rate = sf.read(str(path), dtype="float32", always_2d=False)
    device = cable_play_device()
    target = int(sd.query_devices(device)["default_samplerate"])
    if rate != target:
        audio = np.interp(
            np.arange(0, len(audio), 1 / (target / rate)),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
        rate = target
    sd.play(audio, rate, device=device, blocking=True)
    sd.play(np.zeros(int(rate * 1.2), dtype=np.float32), rate, device=device, blocking=True)


def set_key(key, value):
    out = []
    hit = False
    for line in CFG.read_text(encoding="utf-8").splitlines():
        if line == key or (line.startswith(key + " ") and not line.startswith(key + "-")):
            out.append(key if value == "" else key + " " + value)
            hit = True
        else:
            out.append(line)
    if not hit:
        raise SystemExit("missing " + key)
    CFG.write_text("\n".join(out) + "\n", encoding="utf-8")


def closed_text(path):
    if not path.exists():
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return None
    kernel32.CloseHandle(handle)
    return path.read_text(encoding="utf-8")


def wait_closed(path, proc, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = closed_text(path)
        if text is not None:
            return text
        if proc.poll() is not None:
            return None
        time.sleep(0.2)
    return None


def stop_resident(name, proc):
    (ROOT / (name + ".stop")).write_text("1", encoding="ascii")
    proc.wait()


def wait_pid(name, proc, timeout=120):
    pid = ROOT / (name + ".pid")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pid.exists() and pid.stat().st_size > 0:
            return
        if proc.poll() is not None:
            raise SystemExit(name + " exited before watching")
        time.sleep(0.2)
    raise SystemExit(name + " pid timeout")


def fetch_fixtures():
    CACHE.mkdir(parents=True, exist_ok=True)
    parquet = hf_hub_download(
        repo_id="hf-internal-testing/librispeech_asr_dummy",
        filename="clean/validation-00000-of-00001.parquet",
        repo_type="dataset",
    )
    audio_path = CACHE / (AUDIO_ID + ".flac")
    expected = ""
    for row in pq.read_table(parquet).to_pylist():
        if row["id"] != AUDIO_ID:
            continue
        audio_path.write_bytes(row["audio"]["bytes"])
        expected = row["text"]
        break
    if not expected:
        raise SystemExit("missing utterance " + AUDIO_ID)
    image_src = hf_hub_download(
        repo_id="benwiesel/ScreenSpot",
        filename="images/" + IMAGE_NAME,
        repo_type="dataset",
    )
    image_path = CACHE / IMAGE_NAME
    image_path.write_bytes(Path(image_src).read_bytes())
    listing = hf_hub_download(
        repo_id="benwiesel/ScreenSpot",
        filename="ScreenSpot_combined.json",
        repo_type="dataset",
    )
    import json

    labels = []
    source = ""
    for row in json.loads(Path(listing).read_text(encoding="utf-8")):
        if row.get("image") != IMAGE_NAME:
            continue
        for ann in row["annotations"]:
            source = ann.get("data_source", source)
            labels.append(ann.get("data_type", "") + " " + ann.get("objective_reference", ""))
        break
    if not labels:
        raise SystemExit("missing ScreenSpot labels for " + IMAGE_NAME)
    say("FETCH audio " + AUDIO_ID)
    say("FETCH image " + IMAGE_NAME + " " + source)
    return audio_path, image_path, expected, labels


def demo_ear_live(audio, expected):
    TRACE.begin("ear", "Live ASR on CABLE Output; fixture " + AUDIO_ID)
    err_path = CACHE / "ear.err"
    err = open(err_path, "wb")
    proc = subprocess.Popen(
        [
            str(ROOT / "nemo-speech.exe"),
            "transcribe",
            "--live",
            "--model",
            str(ROOT / "ear.gguf"),
            "--device",
            "cpu",
            "--format",
            "text",
            "--output",
            str(CACHE / "ear.txt"),
            "--force",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    while True:
        heard = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
        if 'listening on "CABLE Output' in heard:
            break
        if proc.poll() is not None:
            raise SystemExit("ear exited before listening")
        time.sleep(0.2)
    play(audio)
    span = sf.info(str(audio)).duration
    while True:
        heard = err_path.read_text(encoding="utf-8", errors="replace")
        times = [float(item) for item in re.findall(r"live final @ ([0-9.]+)s", heard)]
        if times and max(times) >= span:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    if proc.poll() is None:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait()
    heard = err_path.read_text(encoding="utf-8", errors="replace")
    finals = [line.strip() for line in heard.splitlines() if "live final" in line]
    if not finals:
        raise SystemExit("ear heard nothing")
    for line in finals:
        say("EAR " + line)
    TRACE.output("ear", "EXPECT " + expected + "\n" + "\n".join(finals) + "\n")


def demo_mouth(variant, language, sentence):
    set_key("chatterbox.variant", variant)
    set_key("chatterbox.language", language)
    TRACE.begin("mouth-" + variant, variant + " / " + language + ": " + sentence)
    prompt = ROOT / "chatterbox.prompt.txt"
    wav = ROOT / "chatterbox.response.wav"
    reply = ROOT / "chatterbox.response.txt"
    prompt.write_text("", encoding="utf-8")
    wav.unlink(missing_ok=True)
    reply.unlink(missing_ok=True)
    proc = subprocess.Popen([str(ROOT / "chatterbox.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_pid("chatterbox", proc)
        prompt.write_text(sentence, encoding="utf-8")
        while True:
            if closed_text(reply) is not None and wav.exists() and wav.stat().st_size > 44:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if not wav.exists() or wav.stat().st_size <= 44:
            raise SystemExit("mouth " + variant + " missing wav")
        size = wav.stat().st_size
        (TRACE.folder / ("mouth-" + variant) / "response.wav").write_bytes(wav.read_bytes())
        say("MOUTH " + variant + " wav_bytes=" + str(size))
        TRACE.output("mouth-" + variant, sentence + "\nwav_bytes: " + str(size) + "\n")
    finally:
        stop_resident("chatterbox", proc)


def demo_gemma_prompt(label, prompt, proc, why, check=None):
    folder = TRACE.begin("gemma-" + label, why)
    (folder / "prompt.txt").write_bytes(prompt.encode("utf-8"))
    resp = ROOT / "gemma.response.txt"
    resp.unlink(missing_ok=True)
    (ROOT / "gemma.prompt.txt").write_bytes(prompt.encode("utf-8"))
    text = wait_closed(resp, proc, timeout=600)
    if text is None:
        raise SystemExit("gemma " + label + " exited without reply")
    if not text.strip():
        raise SystemExit("gemma " + label + " empty reply")
    if check and not check(text):
        raise SystemExit("gemma " + label + " failed check")
    TRACE.output("gemma-" + label, text)
    say("GEMMA " + label + " ok len=" + str(len(text)))


def demo_gemma_text():
    """One resident, text-only. Uses trident sampling (Gemma 4: temp 1.0 top_p 0.95 top_k 64)."""
    set_key("gemma.image", "")
    (ROOT / "gemma.prompt.txt").write_text("", encoding="utf-8")
    proc = subprocess.Popen([str(ROOT / "gemma-brain.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_pid("gemma", proc)
        demo_gemma_prompt(
            "think",
            PROMPT_THINK,
            proc,
            "Thinking math. Keep trident.txt sampling; do not lower temperature for think.",
            check=lambda t: "21" in t,
        )
        demo_gemma_prompt(
            "tool",
            PROMPT_TOOL,
            proc,
            "Tool add 17+4.",
            check=lambda t: "tool_call" in t and "17" in t and "4" in t,
        )
    finally:
        stop_resident("gemma", proc)


def demo_gemma_image(image_path, labels):
    """Separate process: gemma.image is read only at startup."""
    rel = image_path.relative_to(ROOT)
    set_key("gemma.image", str(rel).replace("\\", "/"))
    (ROOT / "gemma.prompt.txt").write_text("", encoding="utf-8")
    proc = subprocess.Popen([str(ROOT / "gemma-brain.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_pid("gemma", proc, timeout=180)
        say("EXPECT labels " + " | ".join(labels))
        demo_gemma_prompt(
            "image",
            PROMPT_IMAGE,
            proc,
            "ScreenSpot image at gemma.image in settings.txt",
            check=lambda t: len(t.strip()) > 80,
        )
    finally:
        stop_resident("gemma", proc)


def main():
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "sounddevice==0.5.6", "soundfile", "pyarrow==25.0.1", "huggingface_hub"]
    )
    global np, sd, sf, pq, hf_hub_download
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    CACHE.mkdir(parents=True, exist_ok=True)
    global TRACE
    TRACE = Trace(CACHE / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    say("RUN " + str(TRACE.folder))
    original = CFG.read_text(encoding="utf-8")
    laptop = ps("get")
    cable = cable_capture_id()
    try:
        audio, image, expected, labels = fetch_fixtures()
        ps("set", cable)
        demo_ear_live(audio, expected)
        ps("set", laptop)
        demo_mouth("nano", "en", "The tray is red.")
        demo_mouth("turbo", "en", "The tray is red.")
        demo_mouth("v3", "pl", "Pięć żółtych łodzi płynie wzdłuż rzeki.")
        demo_gemma_text()
        demo_gemma_image(image, labels)
    finally:
        CFG.write_text(original, encoding="utf-8")
        ps("set", laptop)
        TRACE.close()
        say("DONE " + str(TRACE.folder))


if __name__ == "__main__":
    main()
