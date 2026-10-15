"""One Trident run under a wall-clock job bomb. Not organism code. Not imported by main.py."""
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / 'runtime' / 'python' / 'Scripts' / 'python.exe'
STOP = ROOT / 'stop.txt'
LOG = ROOT / 'out.txt'
PID_FILE = ROOT / 'supervisor.pid'
STATUS_FILE = ROOT / 'supervisor.status'
LIMIT = 120
GOAL = (
    'Look at the desktop and open Notepad. '
    'Write the true story of this wake as it happens: each thing you attempt, '
    'what you expected, what the screen actually shows, and what remains unfinished. '
    'Save that story as a text file on the desktop, reopen the saved file, '
    'and do not call the goal done until the words on screen match the file.'
)


def append_log(text):
    with open(LOG, 'ab', buffering=0) as handle:
        handle.write(text.encode('utf-8', 'replace'))


def publish(line):
    STATUS_FILE.write_text(line + '\n', encoding='utf-8')
    sys.stdout.write(line + '\n')
    sys.stdout.flush()


def release_input():
    import win32api
    import win32con
    for flag in (win32con.MOUSEEVENTF_LEFTUP, win32con.MOUSEEVENTF_RIGHTUP, win32con.MOUSEEVENTF_MIDDLEUP):
        win32api.mouse_event(flag, 0, 0, 0, 0)
    for key in range(8, 255):
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)


def disarm(job, process):
    try:
        release_input()
    except Exception as exc:
        append_log('--- release failed %s: %s ---\n' % (type(exc).__name__, exc))
    time.sleep(0.05)
    try:
        import win32job
        win32job.TerminateJobObject(job, 1)
    except Exception as exc:
        append_log('--- terminate failed %s: %s ---\n' % (type(exc).__name__, exc))
    try:
        process.wait(timeout=30)
    except Exception as exc:
        append_log('--- wait failed %s: %s ---\n' % (type(exc).__name__, exc))
    try:
        job.Close()
    except Exception as exc:
        append_log('--- job close failed %s: %s ---\n' % (type(exc).__name__, exc))


def line_for(reason, code):
    if reason == 'body-exited' and code == 0:
        return 'DONE body-exited 0'
    if reason == 'body-exited':
        return 'FAILED body-exited %s' % code
    return 'FAILED %s' % reason


def watch():
    import win32api
    import win32con
    import win32job
    if STATUS_FILE.exists():
        STATUS_FILE.unlink()
    PID_FILE.write_text(str(os.getpid()), encoding='ascii')
    if STOP.exists():
        publish('FAILED stop.txt')
        return 1
    if not PYTHON.is_file():
        publish('FAILED runtime-python-missing')
        return 1
    job = win32job.CreateJobObject(None, '')
    win32api.SetHandleInformation(int(job), win32con.HANDLE_FLAG_INHERIT, 0)
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    log = open(LOG, 'ab', buffering=0)
    try:
        log.write(b'--- supervisor start ---\n')
        log.flush()
        command = [str(PYTHON), '-u', '-B', str(ROOT / 'proposed-main.py'), '--fresh', GOAL, '--body']
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=os.environ.copy(), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED)
        log.write(('--- body pid %s ---\n' % process.pid).encode('ascii'))
        log.flush()
        win32job.AssignProcessToJobObject(job, int(process._handle))
        resume = ctypes.OleDLL('ntdll').NtResumeProcess
        resume.argtypes = [ctypes.c_void_p]
        resume.restype = ctypes.c_long
        status = resume(int(process._handle))
        if status < 0:
            process.kill()
            process.wait(timeout=15)
            raise OSError('NtResumeProcess 0x%08X' % (status & 0xFFFFFFFF))
        fired = threading.Event()
        timer = threading.Timer(LIMIT, fired.set)
        timer.daemon = True
        timer.start()
        reason = None
        code = None
        while not fired.is_set():
            if STOP.exists():
                reason = 'stop.txt'
                break
            code = process.poll()
            if code is not None:
                reason = 'body-exited'
                break
            fired.wait(1)
        timer.cancel()
        if reason is None:
            reason = 'deadline'
        disarm(job, process)
        job = None
        code = process.poll()
        result = line_for(reason, code)
    except Exception as exc:
        append_log('--- supervisor error %s: %s ---\n' % (type(exc).__name__, exc))
        if job is not None and 'process' in locals():
            disarm(job, process)
            job = None
        result = 'FAILED %s' % type(exc).__name__
    finally:
        log.close()
        if job is not None:
            try:
                job.Close()
            except Exception:
                pass
    append_log('--- supervisor %s ---\n' % result)
    publish(result)
    return 0 if result.startswith('DONE') else 1


def detach():
    if STOP.exists():
        publish('FAILED stop.txt')
        return 1
    if STATUS_FILE.exists():
        STATUS_FILE.unlink()
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [str(PYTHON), '-u', '-B', str(Path(__file__).resolve()), '--watch'],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(
            [str(PYTHON), '-u', '-B', str(Path(__file__).resolve()), '--watch'],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=flags)
    return 0


if __name__ == '__main__':
    sys.exit(watch() if '--watch' in sys.argv else detach())
