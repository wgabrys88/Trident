import asyncio
import os
import sys
from pathlib import Path

import i2c

TOOLS = ",".join((
    "shell_tool_call", "delete_tool_call", "glob_tool_call", "grep_tool_call",
    "read_tool_call", "update_todos_tool_call", "read_todos_tool_call", "edit_tool_call",
    "ls_tool_call", "read_lints_tool_call", "mcp_tool_call", "sem_search_tool_call",
    "create_plan_tool_call", "web_search_tool_call", "task_tool_call",
    "list_mcp_resources_tool_call", "read_mcp_resource_tool_call", "apply_agent_diff_tool_call",
    "ask_question_tool_call", "fetch_tool_call", "switch_mode_tool_call", "generate_image_tool_call",
    "record_screen_tool_call", "computer_use_tool_call", "write_shell_stdin_tool_call",
    "reflect_tool_call", "setup_vm_environment_tool_call", "truncated_tool_call",
    "start_grind_execution_tool_call", "start_grind_planning_tool_call", "web_fetch_tool_call",
    "report_bugfix_results_tool_call", "ai_attribution_tool_call", "pr_management_tool_call",
    "mcp_auth_tool_call", "await_tool_call", "blame_by_file_path_tool_call", "get_mcp_tools_tool_call",
    "report_bug_tool_call", "set_active_branch_tool_call", "communicate_update_tool_call",
    "send_final_summary_tool_call", "update_pr_code_tour_tool_call", "replace_env_tool_call",
    "edit_pr_labels_tool_call", "record_ci_investigation_findings_tool_call", "send_message_tool_call",
    "fetch_cloud_agent_data_tool_call", "send_to_user_tool_call", "pi_read_tool_call", "pi_bash_tool_call",
    "pi_edit_tool_call", "pi_write_tool_call", "pi_grep_tool_call", "pi_find_tool_call", "pi_ls_tool_call",
    "connect_scm_tool_call", "search_conversations_tool_call", "create_goal_tool_call",
    "update_goal_tool_call", "adopt_tool_call", "get_agent_status_tool_call", "send_to_agent_tool_call",
    "read_agent_transcript_tool_call", "create_agent_tool_call", "stop_agent_tool_call",
    "get_pr_code_tour_tool_call", "write_canvas_tool_call", "read_canvas_tool_call",
))


class Luna:
    def __init__(self, bus, cfg, run, root, command=None):
        self.bus = bus
        self.cfg = cfg
        self.run = Path(run)
        self.root = Path(root)
        self.command = command
        self.tg = i2c.addr(cfg, "telegram")
        self.owners = {i2c.addr(cfg, "telegram"), i2c.addr(cfg, "ears")}
        self.cap = int(cfg["bus"]["self_turn_cap"])
        self.limit = float(cfg["busy"]["luna"])
        self.busy = False
        self.self_turns = 0
        self.transcript = ""
        self.queued = []
        self.count = 0
        self.proc = None

    def argv(self):
        if self.command:
            return list(self.command)
        raw = self.cfg["luna"]["command"]
        folder = Path(os.path.expandvars(str(raw[0])))
        versions = [path for path in folder.iterdir() if (path / "node.exe").is_file() and (path / "index.js").is_file()]
        if not versions:
            raise RuntimeError("Cursor CLI is missing")
        latest = max(versions, key=lambda path: path.name)
        return [
            str(latest / "node.exe"), str(latest / "index.js"), "-p",
            "--model", self.cfg["luna"]["model"], "--output-format", "text",
            "--trust", "--workspace", str(self.run), "--exclude-tools", TOOLS,
        ]

    def stdin_text(self, src, incoming):
        prompt = (self.root / "prompt.txt").read_text(encoding="utf-8")
        listing = "\n".join(f"{int(value, 16):02x} {name}" for name, value in self.cfg["address"].items())
        return prompt + "\n" + listing + "\n" + self.transcript + "\n" + f"{src:02x}\n" + incoming

    async def on_frame(self, src, line):
        if line.split()[2] == "R" and not i2c.write_payload(line):
            return i2c.with_payload(line, bytes([1 if self.busy else 0]))
        if self.busy:
            return i2c.nack(self.bus.addr)
        if src == self.bus.addr:
            self.self_turns += 1
            if self.self_turns > self.cap:
                return i2c.pack_write(self.bus.addr, i2c.write_payload(line))
        elif src in self.owners:
            self.self_turns = 0
        self.busy = True
        data = i2c.write_payload(line)
        asyncio.create_task(self.turn(src, data))
        return i2c.pack_write(self.bus.addr, data)

    async def cli(self, src, incoming):
        text = incoming.decode()
        stdin = self.stdin_text(src, text)
        proc = await asyncio.create_subprocess_exec(
            *self.argv(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(self.run),
        )
        self.proc = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), self.limit)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        finally:
            self.proc = None
        if proc.returncode:
            tail = err.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"exit {proc.returncode}")
        decoded = out.decode()
        self.count += 1
        (self.run / f"turn-{self.count}.txt").write_text(stdin + "\n---\n" + decoded, encoding="utf-8")
        self.transcript += f"In:\n{text}\nOut:\n{decoded}\n"
        return decoded

    async def say(self, text):
        if text.strip():
            await self.bus.request(self.tg, i2c.pack_write(self.tg, bytes([0x10]) + text.encode()))

    async def act(self, out):
        prose = []

        async def flush():
            body = "\n".join(prose).strip()
            prose.clear()
            if body:
                await self.say(body)

        for line in out.splitlines():
            stripped = line.strip()
            if i2c.legal(stripped):
                await flush()
                target = int(stripped.split()[1], 16)
                if target == self.bus.addr and stripped.split()[2] == "W":
                    self.queued.append(stripped)
                    continue
                try:
                    reply = await self.bus.request(target, stripped)
                except Exception as error:
                    self.queued.append(i2c.pack_write(self.bus.addr, str(error).encode()))
                    continue
                if " Sr " in stripped:
                    payload = i2c.read_payload(reply)
                    if payload:
                        self.queued.append(i2c.pack_write(self.bus.addr, payload))
            else:
                prose.append(line)
        await flush()

    async def turn(self, src, data):
        try:
            try:
                out = await self.cli(src, data)
            except TimeoutError:
                self.bus.bump(8)
                await self.say("Luna timed out.")
                return
            await self.act(out)
        except Exception as error:
            self.bus.bump(8)
            await self.say(f"Luna failed: {error}")
        finally:
            queued = self.queued
            self.queued = []
            self.busy = False
            for line in queued:
                self.bus.seq += 1
                i2c.place(self.bus.inbox, f"q-{self.bus.addr:02x}-{self.bus.seq}", line)


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "luna"), run, cfg)
    luna = Luna(bus, cfg, run, root)
    luna.argv()
    i2c.entry(lambda: bus.run(luna.on_frame))


def test():
    import tempfile

    standin = r"""
import sys, time
from pathlib import Path
log, wire, kind = sys.argv[1:]
text = sys.stdin.read()
path = Path(log)
prev = path.read_text(encoding="utf-8") if path.exists() else ""
n = prev.count("BEGIN") + 1
scl = Path(wire) / "scl" / "16"
flag = "SCL" if scl.exists() else "FREE"
path.write_text(prev + f"BEGIN {n} {flag}\n" + text + "\nEND\n", encoding="utf-8")
if kind == "slow":
    time.sleep(0.6)
    sys.stdout.write("hello\n")
elif kind == "self":
    sys.stdout.write("ping\nS 16 W A 68 A 69 A P\n")
elif kind == "bad":
    sys.stdout.write("S 10 W A 03 A P\nkept\n" if n == 1 else "done\n")
else:
    time.sleep(2)
    sys.stdout.write("late\n")
"""

    seen = []

    async def phone(_src, line):
        seen.append(i2c.write_payload(line))
        return i2c.pack_write(0x11, i2c.write_payload(line))

    async def run():
        root = Path(tempfile.mkdtemp())
        run_dir = root / "RUN_test"
        run_dir.mkdir()
        script = root / "standin.py"
        script.write_text(standin, encoding="utf-8")
        log = root / "log.txt"
        wire = root / "wire"
        cfg = i2c.test_cfg()
        cfg["luna"] = {"model": "gpt-5.6-luna-none", "command": ["."]}
        phone_bus = i2c.Bus(root, 0x11, run_dir, cfg)
        phone_bus.up()
        stop = [False]
        phones = asyncio.create_task(i2c._peer(phone_bus, phone, stop))
        here = Path(__file__).resolve().parent

        async def boot(command, this_cfg):
            bus = i2c.Bus(root, 0x16, run_dir, this_cfg)
            mind = Luna(bus, this_cfg, run_dir, here, command)
            task = asyncio.create_task(bus.run(mind.on_frame))
            for _ in range(50):
                if bus.present(0x16):
                    break
                await asyncio.sleep(0.02)
            return bus, mind, task

        slow = [sys.executable, str(script), str(log), str(wire), "slow"]
        bus, mind, task = await boot(slow, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 400
        assert mind.owners == {0x11, 0x12}
        await master.request(0x16, i2c.pack_write(0x16, b"ping"))
        for _ in range(50):
            if log.exists() and "BEGIN" in log.read_text(encoding="utf-8"):
                break
            await asyncio.sleep(0.02)
        nack = await master.transfer(0x16, i2c.pack_write(0x16, b"more"))
        assert nack.split()[3] == "NA"
        assert "FREE" in log.read_text(encoding="utf-8")
        assert not (wire / "scl" / "16").exists()
        for _ in range(80):
            if any(item.startswith(b"\x10hello") for item in seen):
                break
            await asyncio.sleep(0.02)
        assert any(item.startswith(b"\x10hello") for item in seen)
        assert "\n11\nping\n" in (run_dir / "turn-1.txt").read_text(encoding="utf-8")
        assert "10 timer" in (run_dir / "turn-1.txt").read_text(encoding="utf-8")
        assert " ack " in (run_dir / "bus.log").read_text(encoding="utf-8")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["bus"]["self_turn_cap"] = 2
        seen.clear()
        log.write_text("", encoding="utf-8")
        chain = [sys.executable, str(script), str(log), str(wire), "self"]
        bus, mind, task = await boot(chain, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 800
        await master.request(0x16, i2c.pack_write(0x16, b"go"))
        for _ in range(100):
            if log.exists() and log.read_text(encoding="utf-8").count("BEGIN") >= 3:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        assert log.read_text(encoding="utf-8").count("BEGIN") == 3
        assert sum(1 for item in seen if item.startswith(b"\x10ping")) == 3
        other = i2c.Bus(root, 0x13, run_dir, cfg)
        other.seq = 900
        await other.request(0x16, i2c.pack_write(0x16, b"side"))
        await asyncio.sleep(0.3)
        assert log.read_text(encoding="utf-8").count("BEGIN") == 4
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["bus"]["self_turn_cap"] = 4
        seen.clear()
        log.write_text("", encoding="utf-8")
        bad = [sys.executable, str(script), str(log), str(wire), "bad"]
        bus, mind, task = await boot(bad, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 1000
        await master.request(0x16, i2c.pack_write(0x16, b"go"))
        for _ in range(80):
            if any(item.startswith(b"\x10done") for item in seen):
                break
            await asyncio.sleep(0.02)
        assert any(item.startswith(b"\x10kept") for item in seen)
        assert any(item.startswith(b"\x10done") for item in seen)
        assert not any(item.startswith(b"\x10Luna failed") for item in seen)
        assert "NACK 10" in (run_dir / "turn-2.txt").read_text(encoding="utf-8")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        cfg["busy"]["luna"] = 0.3
        seen.clear()
        hung = [sys.executable, str(script), str(log), str(wire), "hang"]
        bus, mind, task = await boot(hung, cfg)
        master = i2c.Bus(root, 0x11, run_dir, cfg)
        master.seq = 1200
        await master.request(0x16, i2c.pack_write(0x16, b"wait"))
        for _ in range(80):
            if any(item.startswith(b"\x10Luna timed out.") for item in seen):
                break
            await asyncio.sleep(0.05)
        assert any(item.startswith(b"\x10Luna timed out.") for item in seen)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        stop[0] = True
        await phones
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
