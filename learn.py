import ctypes, json, sys, time
from pathlib import Path
from acts import NAMES, PARAMS
from core import ROOT, STATE, encode
from engines import ask

STUDY = "Rewrite the study notes shorter by meaning. Keep every fact, decision, place, and open step from the notes and from the new material. Return only the notes."

def running(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True

def take(text):
    data = json.loads(text)
    prompt, tools, teaching = data["prompt"], data["tools"], data["teaching"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("Lesson prompt is empty")
    if not isinstance(teaching, str) or not teaching.strip():
        raise RuntimeError("Lesson teaching is empty")
    if set(tools) != set(NAMES):
        raise RuntimeError("Lesson tool names do not match the mechanism")
    for name, spec in tools.items():
        if set(spec["parameters"]) != set(PARAMS[name]):
            raise RuntimeError(f"{name} parameters do not match the mechanism")
        if not isinstance(spec.get("description"), str) or not spec["description"].strip():
            raise RuntimeError(f"{name} description is empty")
        for key, value in spec["parameters"].items():
            if not isinstance(value, dict) or not isinstance(value.get("description"), str) or not value["description"].strip():
                raise RuntimeError(f"{name}.{key} description is empty")
    return prompt, tools, teaching

def notes():
    path = STATE / "learn.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""

def save(text):
    path = STATE / "learn.txt"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)

def study(material):
    save(ask([
        {"role": "system", "content": STUDY},
        {"role": "user", "content": f"Notes:\n{notes()}\n\nNew material:\n{material}"}]))

def publish(prompt, tools):
    for path, text in ((ROOT / "organism.txt", prompt), (ROOT / "tools.json", encode(tools))):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)

def apply(path):
    prompt, tools, teaching = take(path.read_text(encoding="utf-8"))
    publish(prompt, tools)
    study(f"Teaching:\n{teaching}\n\nPrompt now:\n{prompt}\n\nTools now:\n{encode(tools)}")
    marker = path.with_suffix(".applied")
    temporary = path.with_suffix(".applied.tmp")
    temporary.write_text(teaching, encoding="utf-8")
    temporary.replace(marker)

def mark(folder, name):
    path = folder / "studied"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(name, encoding="utf-8")
    temporary.replace(path)

def main():
    folder, pid = Path(sys.argv[1]), int(sys.argv[2])
    (folder / "learner.ready").write_text("up", encoding="utf-8")
    seen = set()
    while running(pid):
        gpt = folder / "gpt"
        if gpt.exists():
            for path in sorted(gpt.glob("*.json")):
                key = ("gpt", path.name)
                if key not in seen:
                    study(path.read_text(encoding="utf-8"))
                    mark(folder, path.name)
                    seen.add(key)
        teach = folder / "teach"
        if teach.exists():
            for path in sorted(teach.glob("*.json")):
                key = ("teach", path.name)
                if key not in seen:
                    apply(path)
                    seen.add(key)
        time.sleep(0.05)

if __name__ == "__main__":
    main()
