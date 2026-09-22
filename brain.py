import ctypes, sys
from ctypes import wintypes
from install import MODELS, reexec
from settings import BRAIN, SPEAK, TOOLS
PIPE = r"\\.\pipe\trident-brain"
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes, K32.WaitNamedPipeW.restype = [ctypes.c_wchar_p, ctypes.c_uint], ctypes.c_int
K32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
K32.CreateNamedPipeW.restype = wintypes.HANDLE
K32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
K32.ConnectNamedPipe.restype = wintypes.BOOL
K32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
K32.ReadFile.restype = wintypes.BOOL
K32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
K32.WriteFile.restype = wintypes.BOOL
K32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
K32.FlushFileBuffers.restype = wintypes.BOOL
K32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
K32.DisconnectNamedPipe.restype = wintypes.BOOL
INVALID = ctypes.c_void_p(-1).value
def read_exact(handle, count: int) -> bytes:
    buf, got = bytearray(), wintypes.DWORD()
    while len(buf) < count:
        chunk = ctypes.create_string_buffer(count - len(buf))
        if not K32.ReadFile(handle, chunk, len(chunk), ctypes.byref(got), None) or not got.value:
            raise BrokenPipeError(PIPE)
        buf += chunk.raw[:got.value]
    return bytes(buf)
def read_line(handle) -> str:
    line, one, got = bytearray(), ctypes.create_string_buffer(1), wintypes.DWORD()
    while True:
        if not K32.ReadFile(handle, one, 1, ctypes.byref(got), None) or not got.value:
            raise BrokenPipeError(PIPE)
        if one.raw[0] == 10:
            return line.decode("utf-8")
        line += one.raw[:1]
def write_all(handle, payload: bytes):
    view, got = payload, wintypes.DWORD()
    while view:
        chunk = ctypes.create_string_buffer(view)
        if not K32.WriteFile(handle, chunk, len(view), ctypes.byref(got), None) or not got.value:
            raise BrokenPipeError(PIPE)
        view = view[got.value:]
def ask(text: str) -> str:
    if not K32.WaitNamedPipeW(PIPE, 60000):
        raise RuntimeError("brain: pipe " + PIPE)
    payload = text.encode("utf-8")
    with open(PIPE, "r+b", buffering=0) as stream:
        message = memoryview(f"{len(payload)}\n".encode("utf-8") + payload)
        while message:
            sent = stream.write(message)
            if not sent:
                raise BrokenPipeError(PIPE)
            message = message[sent:]
        count = int(stream.readline().decode("utf-8").strip())
        buf = bytearray()
        while len(buf) < count:
            chunk = stream.read(count - len(buf))
            if not chunk:
                raise BrokenPipeError(PIPE)
            buf += chunk
    return bytes(buf).decode("utf-8")
def serve():
    path = MODELS / BRAIN["file"]
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    from llama_cpp import Llama, LlamaGrammar
    llm = Llama(model_path=str(path), n_ctx=BRAIN["n_ctx"], n_threads=BRAIN["n_threads"],
                n_gpu_layers=BRAIN["n_gpu_layers"], chat_format="chat_template.default", verbose=False)
    grammar = LlamaGrammar.from_string(r'''
root ::= call+
call ::= say | bare
say ::= "<|tool_call>call:say{" saybody "}" "<tool_call|>"
bare ::= "<|tool_call>call:" ("listen" | "quit") "{}" "<tool_call|>"
saybody ::= "text:" parts ",language:" lang | "language:" lang ",text:" parts
parts ::= "[" piece ("," piece)* "]"
piece ::= mark chars mark
lang ::= mark ("en" | "pl") mark
mark ::= "<|\"|>"
chars ::= [^<]*
''')
    handle = K32.CreateNamedPipeW(PIPE, 3, 8, 1, 1 << 20, 1 << 20, 0, None)
    if handle is None or handle == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    print("ready", flush=True)
    while True:
        if not K32.ConnectNamedPipe(handle, None) and ctypes.get_last_error() != 535:
            raise ctypes.WinError(ctypes.get_last_error())
        count = int(read_line(handle))
        user = read_exact(handle, count).decode("utf-8")
        content = llm.create_chat_completion(
            messages=[{"role": "system", "content": SPEAK}, {"role": "user", "content": user}],
            tools=TOOLS, stop=["<turn|>"], grammar=grammar, **BRAIN["decode"]
        )["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("brain completion")
        payload = content.encode("utf-8")
        write_all(handle, f"{len(payload)}\n".encode("utf-8") + payload)
        if not K32.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        if not K32.DisconnectNamedPipe(handle):
            raise ctypes.WinError(ctypes.get_last_error())
if __name__ == "__main__":
    reexec()
    if len(sys.argv) == 1:
        serve()
    elif len(sys.argv) == 2 and sys.argv[1]:
        print(ask(sys.argv[1]), flush=True)
    else:
        raise SystemExit("usage: python brain.py [text]")
