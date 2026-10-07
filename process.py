import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import win32api
import win32con
import win32event
import win32job
import win32process
import win32security
import win32service
import ntsecuritycon
import pywintypes

from store import CONFIG, ROOT, command, encode, source_entries

PROTECTED = False
RESTRICTING_SID = "S-1-5-21-" + "-".join(str(int.from_bytes(os.urandom(4), "little")) for _ in range(3)) + "-1001"


def label(path, level):
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"S:(ML;OICI;NW;;;{level})", win32security.SDDL_REVISION_1)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.LABEL_SECURITY_INFORMATION, None, None, None, descriptor.GetSecurityDescriptorSacl())


def plain_tree(path):
    if path.lstat().st_file_attributes & 1024:
        raise RuntimeError(f"Reparse point refused: {path}")
    for folder, directories, files in os.walk(path):
        for name in [*directories, *files]:
            item = Path(folder) / name
            try:
                metadata = item.lstat()
            except FileNotFoundError:
                continue  # Atomic runtime state replacements may remove temporary entries.
            if metadata.st_file_attributes & 1024 or (item.is_file() and metadata.st_nlink != 1):
                raise RuntimeError(f"Filesystem alias refused: {item}")
            yield item


def allow(path, sid, access):
    security = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                 win32security.DACL_SECURITY_INFORMATION)
    acl = security.GetSecurityDescriptorDacl()
    if acl is None:
        raise RuntimeError("A protected path may not have an unrestricted DACL")
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, 3 if path.is_dir() else 0, access, sid)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                      win32security.DACL_SECURITY_INFORMATION, None, None, acl, None)


def workspace(folder):
    global PROTECTED
    sid = win32security.ConvertStringSidToSid(RESTRICTING_SID)
    if not PROTECTED:
        for ancestor in (ROOT, *ROOT.parents):
            if ancestor.lstat().st_file_attributes & 1024:
                raise RuntimeError("The protected tree must not have reparse ancestors")
        for path in (ROOT.parent, ROOT, *source_entries(git=True), ROOT / "runs", *plain_tree(ROOT / "runs")):
            try:
                label(path, "ME")
            except pywintypes.error as error:
                if error.winerror not in (2, 3) or not path.is_relative_to(ROOT / "runs"):
                    raise
        allow(ROOT, sid, ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_EXECUTE)
        for path in source_entries(git=True):
            allow(path, sid, ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_EXECUTE)
        for path in {Path(part).parent for part in command(CONFIG["luna"]["command"])}:
            if not path.is_relative_to(ROOT):
                allow(path, sid, ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_EXECUTE)
        PROTECTED = True
    base = Path(os.environ["USERPROFILE"]) / "AppData" / "LocalLow" / "Trident"
    base.mkdir(parents=True, exist_ok=True)
    for ancestor in (base, *base.parents):
        if ancestor.lstat().st_file_attributes & 1024:
            raise RuntimeError("Scratch must not have reparse ancestors")
    scratch = base / uuid.uuid4().hex[:12]
    scratch.mkdir()
    # A unique restricting SID passes write checks only in this scratch tree.
    allow(scratch, sid, ntsecuritycon.FILE_ALL_ACCESS)
    label(scratch, "LW")
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "NODE_PATH"):
        environment.pop(key, None)
    environment.update(CURSOR_CONFIG_DIR=str(scratch / ".cursor"), CURSOR_DATA_DIR=str(scratch / ".cursor"),
        USERPROFILE=str(scratch), HOME=str(scratch), APPDATA=str(scratch / "AppData"),
        LOCALAPPDATA=str(scratch / "Local"), TEMP=str(scratch), TMP=str(scratch),
        PYTHONDONTWRITEBYTECODE="1", TRIDENT_RESTRICTING_SID=RESTRICTING_SID)
    sources = [(Path.home() / ".cursor" / "cli-config.json", scratch / ".cursor" / "cli-config.json"),
               (Path(os.environ["APPDATA"]) / "Cursor" / "auth.json", scratch / "AppData" / "Cursor" / "auth.json")]
    for source, target in sources:
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    for path in plain_tree(scratch):
        label(path, "LW")
    return scratch, environment


async def execute(parts, data, folder, record, low=False, stream=False, computation=False):
    parts = command(parts)
    environment, scratch = os.environ.copy(), ROOT
    if low:
        setup = asyncio.create_task(asyncio.to_thread(workspace, folder))
        try:
            scratch, environment = await asyncio.shield(setup)
        except asyncio.CancelledError:
            scratch, _ = await setup
            list(plain_tree(scratch))
            if scratch.resolve().parent != Path(os.environ["USERPROFILE"]) / "AppData" / "LocalLow" / "Trident":
                raise RuntimeError("Unexpected scratch path")
            shutil.rmtree(scratch)
            raise
        if "--workspace" in parts:
            parts[parts.index("--workspace") + 1] = str(scratch)
        request = {"code": data.decode("utf-8")} if computation else {"command": parts, "input": data.decode("utf-8")}
        data = encode(request).encode("utf-8")
        parts = [sys.executable, "-B", str(Path(__file__).resolve())]
    job, process, readers, output = None, None, [], []
    try:
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        process = await asyncio.create_subprocess_exec(*parts, cwd=scratch, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        except BaseException:
            process.kill()
            raise
        finally:
            handle.Close()

        async def consume(pipe, channel):
            pending = b""
            def capture(raw):
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if channel == "stdout":
                    output.append(text)
                value = text
                if stream and channel == "stdout":
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError:
                        pass
                event = {"channel": channel, "data": value}
                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    event["raw_base64"] = base64.b64encode(raw).decode("ascii")
                record.append("model_event" if stream else "process_output", event,
                              "LUNA" if stream else "PROCESS", "TRIDENT")
            while chunk := await pipe.read(65536):
                pending += chunk
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    capture(raw)
            if pending:
                capture(pending)

        async def wait_exit():
            while process.returncode is None:
                await asyncio.sleep(0.05)
            # A detached descendant must not keep the pipes or visit alive.
            win32job.TerminateJobObject(job, 1)
            await process.wait()
        readers = [asyncio.create_task(consume(process.stdout, "stdout")),
                   asyncio.create_task(consume(process.stderr, "stderr")), asyncio.create_task(wait_exit())]
        process.stdin.write(data)
        await process.stdin.drain()
        process.stdin.close()
        done, _ = await asyncio.wait(readers, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
        await asyncio.gather(*readers)
        record.append("process_exit", {"command": parts, "exit": process.returncode})
        if process.returncode:
            raise RuntimeError(f"Process exited {process.returncode}; see recorded output")
        return "\n".join(output)
    finally:
        if job is not None:
            job.Close()
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        if low and scratch != ROOT:
            # Never follow worker-created junctions during cleanup.
            list(plain_tree(scratch))
            if scratch.parent != Path(os.environ["USERPROFILE"]) / "AppData" / "LocalLow" / "Trident":
                raise RuntimeError("Unexpected scratch path")
            shutil.rmtree(scratch)


def restricted(request):
    import msvcrt
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_ALL_ACCESS)
    restricted_token = None
    station, desktop = None, None
    try:
        if win32security.GetTokenInformation(token, win32security.TokenElevation):
            raise RuntimeError("Run Trident as a standard user, not elevated")
        sid = win32security.ConvertStringSidToSid(os.environ["TRIDENT_RESTRICTING_SID"])
        privileges = win32security.GetTokenInformation(token, win32security.TokenPrivileges)
        # Both read and write checks use restricting SIDs. In particular this
        # denies opening the unrestricted parent's process/token to regain it.
        infrastructure = [win32security.CreateWellKnownSid(kind, None) for kind in
                          (win32security.WinWorldSid, win32security.WinBuiltinUsersSid)]
        privileges = [(luid, attributes) for luid, attributes in privileges
                      if win32security.LookupPrivilegeName(None, luid) != "SeChangeNotifyPrivilege"]
        restricted_token = win32security.CreateRestrictedToken(token, 0, [], privileges,
                                                               [(sid, 0), *((item, 0) for item in infrastructure)])
        low_sid = win32security.ConvertStringSidToSid("S-1-16-4096")
        win32security.SetTokenInformation(restricted_token, win32security.TokenIntegrityLevel,
                                         (low_sid, win32security.SE_GROUP_INTEGRITY))
        acl = win32security.GetTokenInformation(restricted_token, win32security.TokenDefaultDacl)
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, sid)
        win32security.SetTokenInformation(restricted_token, win32security.TokenDefaultDacl, acl)
        path = Path.cwd() / "request.json"
        path.write_text(encode(request), encoding="utf-8")
        user, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        user_text = win32security.ConvertSidToStringSid(user)
        security = pywintypes.SECURITY_ATTRIBUTES()
        security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;GA;;;{user_text})(A;;GA;;;{os.environ['TRIDENT_RESTRICTING_SID']})S:(ML;;NW;;;LW)",
            win32security.SDDL_REVISION_1)
        station_name = "Trident-" + uuid.uuid4().hex
        station = win32service.CreateWindowStation(station_name, 0, win32con.GENERIC_ALL, security)
        original = win32service.GetProcessWindowStation()
        try:
            station.SetProcessWindowStation()
            desktop = win32service.CreateDesktop("mind", 0, win32con.GENERIC_ALL, security)
        finally:
            original.SetProcessWindowStation()
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = station_name + "\\mind"
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdInput = msvcrt.get_osfhandle(sys.stdin.fileno())
        startup.hStdOutput = msvcrt.get_osfhandle(sys.stdout.fileno())
        startup.hStdError = msvcrt.get_osfhandle(sys.stderr.fileno())
        parts = [sys.executable, "-B", str(Path(__file__).resolve()), "--restricted", str(path)]
        child, thread, _, _ = win32process.CreateProcessAsUser(restricted_token, None,
            subprocess.list2cmdline(parts), None, None, True, subprocess.CREATE_NO_WINDOW, None, str(Path.cwd()), startup)
        try:
            win32event.WaitForSingleObject(child, win32event.INFINITE)
            return win32process.GetExitCodeProcess(child)
        finally:
            thread.Close()
            child.Close()
    finally:
        if desktop is not None:
            desktop.CloseDesktop()
        if station is not None:
            station.CloseWindowStation()
        if restricted_token is not None:
            restricted_token.Close()
        token.Close()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Parent assigns the kill-on-close job before releasing this handshake.
        sys.exit(restricted(json.loads(sys.stdin.buffer.read())))
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
        groups = win32security.GetTokenInformation(token, win32security.TokenRestrictedSids)
        if win32security.ConvertSidToStringSid(sid) != "S-1-16-4096" or not any(
            win32security.ConvertSidToStringSid(group) == os.environ["TRIDENT_RESTRICTING_SID"] for group, _ in groups):
            raise RuntimeError("Restricted low-integrity execution was not established")
    finally:
        token.Close()
    request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if "command" in request:
        sys.exit(subprocess.run(request["command"], input=request["input"].encode("utf-8"),
                                creationflags=subprocess.CREATE_NO_WINDOW).returncode)
    namespace = {"asyncio": asyncio, "ROOT": ROOT, "Path": Path}
    exec(compile("async def action():\n" + "\n".join("    " + line for line in request["code"].splitlines()),
                 "<luna-computation>", "exec"), namespace)
    print(encode({"result": asyncio.run(namespace["action"]())}))
