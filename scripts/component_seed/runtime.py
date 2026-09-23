from trident_lib import DONE, MODELS, ROOT, WORK, http, kill_server_pid, reexec, venv_python

BUILD = ROOT / "build" / "bin"


def kill_chatterbox() -> None:
    kill_server_pid()


class Http:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, payload=None, timeout=70):
        return http(self.base, method, path, payload, timeout)
