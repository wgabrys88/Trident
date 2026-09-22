import json, subprocess, sys, time
from pathlib import Path
from settings import MEMORY, sweep_queue

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
TRIDENT_PID = MODELS / "trident.pid"

def ps(command):
    print(">>", command[:160].replace("\n", " "), flush=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=False)

def drop(name, text):
    command = (
        "New-Item -ItemType Directory -Force -Path .\\workspace\\inbox | Out-Null; "
        "@'\n" + text + "\n'@ | Set-Content -Encoding utf8 .\\workspace\\inbox\\" + name
    )
    ps(command)
    time.sleep(10)

print("zero wipe leftover queue", flush=True)
sweep_queue()
MEMORY.write_text("", encoding="utf-8")

print("zero install asr brain nano", flush=True)
subprocess.run([sys.executable, str(ROOT / "install.py"), "nano"], check=False)
time.sleep(10)

MODELS.mkdir(parents=True, exist_ok=True)
trident = subprocess.Popen([sys.executable, str(ROOT / "trident.py"), "nano", "inbox"], cwd=str(ROOT))
TRIDENT_PID.write_text(json.dumps({"pid": trident.pid}) + "\n", encoding="utf-8")
time.sleep(10)

drop("short.txt", "Please say this aloud.\n\nHello Jarvis. Say only this sentence and then be quiet.")
drop(
    "ezekiel.txt",
    "Please say this aloud.\n\n"
    "Ezekiel 25:17. And I will execute great vengeance upon them with furious rebukes; "
    "and they shall know that I am the LORD, when I shall lay my vengeance upon them.\n\n"
    "The Lord is my shepherd; I shall not want. He maketh me to lie down in green pastures: "
    "he leadeth me beside the still waters. He restoreth my soul: he leadeth me in the paths "
    "of righteousness for his name's sake. Yea, though I walk through the valley of the shadow "
    "of death, I will fear no evil: for thou art with me; thy rod and thy staff they comfort me. "
    "Thou preparest a table before me in the presence of mine enemies: thou anointest my head "
    "with oil; my cup runneth over. Surely goodness and mercy shall follow me all the days of "
    "my life: and I will dwell in the house of the LORD for ever.\n\n"
    "In the beginning God created the heaven and the earth. And the earth was without form, "
    "and void; and darkness was upon the face of the deep. And the Spirit of God moved upon "
    "the face of the waters. And God said, Let there be light: and there was light. And God "
    "saw the light, that it was good: and God divided the light from the darkness. And God "
    "called the light Day, and the darkness he called Night. And the evening and the morning "
    "were the first day.",
)
drop("ask.txt", "What is a named pipe? Answer aloud, short.")
drop("pl.txt", "Powiedz to glosno po polsku. Jestem Jarvis. To tylko jeden krotki test mowy.")
drop("name.txt", "My name is Wojciech. Keep that. Do not speak unless you must.")
drop("distill.txt", "Concatenate your memory. Distill only names and decisions. Do not keep pages that were only to be spoken. Do not speak.")
drop("py.txt", "Run Python that writes pong.txt in the workspace folder with the text pong and prints the folder listing. Then say one sentence that it worked.")
drop("stop.txt", "Quit. Leave now.")
print("done", flush=True)
