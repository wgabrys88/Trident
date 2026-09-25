# Run after python install.py. No arguments.
# Downloads stay under .install/harness and are not committed.
# Both machines have VB-Cable. The ear hears only CABLE Output.
#
# Audio: https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy
#        clean/validation-00000-of-00001.parquet  id 1272-128104-0004
# Image: https://huggingface.co/datasets/benwiesel/ScreenSpot
#        images/pc_6f79b56c-2b0f-471d-9f9d-93932c69a0ce.png
#        labels: ScreenSpot_combined.json for that filename

import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".install" / "harness"
CFG = ROOT / "trident.txt"
AUDIO_ID = "1272-128104-0004"
IMAGE_NAME = "pc_6f79b56c-2b0f-471d-9f9d-93932c69a0ce.png"
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


def ps(mode, device=""):
    script = CACHE / "mic.ps1"
    script.write_text(MIC_PS, encoding="utf-8")
    done = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), "-Mode", mode, "-Id", device],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise SystemExit(done.stderr.strip() or "mic switch failed")
    return done.stdout.strip()


def cable_capture_id():
    done = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$base='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Capture';"
         "Get-ChildItem $base | ForEach-Object {"
         "$p=Get-ItemProperty (Join-Path $_.PSPath 'Properties');"
         "$n=$p.'{a45c254e-df1c-4efd-8020-67d146a850e0},2';"
         "if ($n -is [byte[]]) { $n=[Text.Encoding]::Unicode.GetString($n) };"
         "$n=($n -replace \"`0\", '').Trim();"
         "$state=(Get-ItemProperty $_.PSPath).DeviceState;"
         "if ($state -eq 1 -and $n -eq 'CABLE Output') { '{0.0.1.00000000}.' + $_.PSChildName }"
         "}"],
        capture_output=True, text=True,
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


def stop(name):
    (ROOT / (name + ".stop")).write_text("1", encoding="ascii")
    deadline = time.time() + 40
    while time.time() < deadline:
        alive = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq " + name + ".exe", "/NH"],
            capture_output=True, text=True,
        ).stdout
        if name + ".exe" not in alive:
            return
        time.sleep(0.2)
    raise SystemExit(name + " still running")


def fetch():
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
    say("FETCH audio " + AUDIO_ID + " " + expected)
    say("FETCH image " + IMAGE_NAME + " " + source + " " + " | ".join(labels))
    return audio_path, image_path, expected, labels


def ear(audio, expected):
    err_path = CACHE / "ear.err"
    err = open(err_path, "wb")
    proc = subprocess.Popen(
        [str(ROOT / "nemo-speech.exe"), "transcribe", "--live",
         "--model", str(ROOT / "ear.gguf"), "--device", "cpu",
         "--format", "text", "--output", str(CACHE / "ear.txt"), "--force"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        heard = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
        if 'listening on "CABLE Output' in heard:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    play(audio)
    time.sleep(2)
    proc.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    heard = err_path.read_text(encoding="utf-8", errors="replace")
    say("EXPECT " + expected)
    for line in heard.splitlines():
        if "live final" in line:
            say("EAR " + line.strip())


def mouth(variant, language, sentence):
    set_key("chatterbox.variant", variant)
    set_key("chatterbox.language", language)
    prompt = ROOT / "chatterbox.prompt.txt"
    wav = ROOT / "chatterbox.response.wav"
    if wav.exists():
        wav.unlink()
    prompt.write_text(sentence, encoding="utf-8")
    now = time.time()
    err_path = CACHE / (variant + ".err")
    err = open(err_path, "wb")
    proc = subprocess.Popen([str(ROOT / "chatterbox.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=err)
    deadline = time.time() + 180
    size = 0
    while time.time() < deadline:
        if wav.exists() and wav.stat().st_mtime >= now - 1 and wav.stat().st_size > 44:
            size = wav.stat().st_size
            break
        time.sleep(0.2)
    if size:
        (CACHE / (variant + ".wav")).write_bytes(wav.read_bytes())
    stop("chatterbox")
    proc.wait(timeout=20)
    tail = err_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    say("MOUTH " + variant + " " + language + " wav_bytes=" + str(size) + " " + (tail[-1][:160] if tail else ""))


def ask(label, prompt, timeout):
    resp = ROOT / "gemma.response.txt"
    if resp.exists():
        resp.unlink()
    (ROOT / "gemma.prompt.txt").write_bytes(prompt.encode("utf-8"))
    now = time.time()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if resp.exists() and resp.stat().st_mtime >= now - 1 and resp.stat().st_size > 0:
            say("GEMMA " + label + " " + resp.read_text(encoding="utf-8").replace("\n", " | "))
            return
        time.sleep(0.2)
    say("GEMMA " + label + " empty")


def gemma(image, labels):
    set_key("gemma.temp", "0.2")
    set_key("gemma.seed", "1")
    set_key("gemma.n-predict", "80")
    set_key("gemma.image", "")
    err_path = CACHE / "gemma.err"
    err = open(err_path, "wb")
    proc = subprocess.Popen([str(ROOT / "gemma-brain.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=err)
    ask("think", "<|turn>system\n<|think|>Reply with digits only after the thought.<turn|>\n<|turn>user\nWhat is 17 plus 4?<turn|>\n<|turn>model\n", 240)
    ask("tool", "<|turn>system\n<|think|>You are a helpful assistant.<|tool>declaration:add{a:<|\"|>number<|\"|>,b:<|\"|>number<|\"|>}<tool|><turn|>\n<|turn>user\nAdd 17 and 4.<turn|>\n<|turn>model\n", 180)
    stop("gemma")
    proc.wait(timeout=30)
    set_key("gemma.image", str(image))
    err = open(err_path, "ab")
    proc = subprocess.Popen([str(ROOT / "gemma-brain.exe")], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=err)
    say("EXPECT screen " + " | ".join(labels))
    ask("image", "<|turn>user\nWhat application is this screen, and which controls can be clicked?<turn|>\n<|turn>model\n", 240)
    stop("gemma")
    proc.wait(timeout=30)


def main():
    subprocess.check_call([sys.executable, "-m", "pip", "install", "sounddevice==0.5.6", "pyarrow==25.0.1"])
    global np, sd, sf, pq, hf_hub_download
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download
    CACHE.mkdir(parents=True, exist_ok=True)
    original = CFG.read_text(encoding="utf-8")
    laptop = ps("get")
    cable = cable_capture_id()
    try:
        audio, image, expected, labels = fetch()
        ps("set", cable)
        ear(audio, expected)
        ps("set", laptop)
        mouth("nano", "en", "The tray is red.")
        mouth("turbo", "en", "The tray is red.")
        mouth("v3", "pl", "Pięć żółtych łodzi płynie wzdłuż rzeki.")
        gemma(image, labels)
    finally:
        CFG.write_text(original, encoding="utf-8")
        ps("set", laptop)
        say("HARNESS restored trident.txt and the microphone")


if __name__ == "__main__":
    main()
