import json, sys, time, winsound
from datetime import datetime, timezone

def signal(minutes):
    started = time.monotonic()
    for minute in range(1, minutes + 1):
        time.sleep(max(0, started + minute * 60 - time.monotonic()))
        for _ in range(minute): winsound.Beep(880, 150); time.sleep(0.25)
        print(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), minute=minute, elapsed=time.monotonic() - started, pulses=minute, output='speaker', hearing_verified=False)), flush=True)

if __name__ == '__main__':
    try: signal(int(sys.argv[1]))
    except KeyboardInterrupt: pass
