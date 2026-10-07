import asyncio
import ast
import difflib
import hashlib
from importlib.machinery import PathFinder
import json
import os
import stat
import sys
import tomllib
import uuid
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator

from line import Line
from models import decision
from store import ROOT, RUNTIME, Record, read, save, source_paths, write


def snapshot(path):
    manifest = {}
    temporary = path.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for folder, directories, files in os.walk(ROOT, followlinks=False):
            directories[:] = [name for name in directories if name != "__pycache__"
                              and (Path(folder) != ROOT or name not in RUNTIME)]
            for name in [*directories, *files]:
                item = Path(folder) / name
                relative = item.relative_to(ROOT).as_posix()
                if item.lstat().st_file_attributes & 1024:
                    manifest[relative] = {"kind": "alias", "target": os.readlink(item)}
                    if name in directories:
                        directories.remove(name)
                elif item.is_dir():
                    manifest[relative] = {"kind": "directory"}
                    archive.writestr(relative + "/", b"")
                else:
                    data = item.read_bytes()
                    manifest[relative] = {"kind": "file", "sha256": hashlib.sha256(data).hexdigest(),
                                          "links": item.stat().st_nlink}
                    archive.writestr(relative, data)
    with temporary.open("rb+") as stream:
        os.fsync(stream.fileno())
    temporary.replace(path)
    return manifest


def validate():
    trees = {}
    for path in source_paths():
        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
            trees[path.relative_to(ROOT).as_posix()] = ast.parse(source, str(path))
        elif path.suffix == ".json":
            read(path)
        elif path.suffix == ".toml":
            tomllib.loads(path.read_text(encoding="utf-8"))
    local = {Path(name).parts[0].removesuffix(".py") for name in trees}
    for tree in trees.values():
        for node in ast.walk(tree):
            names = [item.name.split(".")[0] for item in node.names] if isinstance(node, ast.Import) else (
                [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
            for name in names:
                if name not in local and name not in sys.stdlib_module_names and PathFinder.find_spec(name) is None:
                    raise ValueError(f"Import dependency is unavailable: {name}")
    document = read(ROOT / "tools.json")
    Draft202012Validator.check_schema({"$defs": document["$defs"], "$ref": "#/$defs/batch"})
    for name, spec in document["tools"].items():
        schema = {"$defs": document["$defs"], **spec["parameters"]}
        Draft202012Validator.check_schema(schema)
        def references(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    target = document
                    for part in value["$ref"].removeprefix("#/").split("/"):
                        target = target[part]
                for child in value.values():
                    references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        references(schema)
        module, function = spec["handler"].split(":", 1)
        if module not in ("tools", "desktop", "line"):
            raise ValueError("Unsupported tool handler module")
        nodes = trees[module + ".py"].body
        if module == "line":
            nodes = next(node.body for node in nodes if isinstance(node, ast.ClassDef) and node.name == "Line")
        handler = next(node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function)
        arguments = handler.args
        names = {argument.arg for argument in [*arguments.args, *arguments.kwonlyargs]} - {"host", "self"}
        if arguments.posonlyargs or arguments.vararg or arguments.kwarg or names != set(spec["parameters"]["properties"]):
            raise ValueError(f"Tool/handler arguments differ: {name}")
        if set(spec["parameters"].get("required", [])) != names or spec["parameters"].get("additionalProperties") is not False:
            raise ValueError(f"Tool contract must specify every argument: {name}")
        if not isinstance(spec["boundary"], bool) or not isinstance(spec["observe"], bool):
            raise ValueError("Tool effects must be explicit")
        if module == "desktop" and not spec["observe"]:
            raise ValueError("Desktop mutations need later PNG receipts")
    for required in ("launch.py", "trident.py", "repair.py", "process.py", "agent.py", "audio.py", "desktop.py",
                     "line.py", "models.py", "store.py", "tools.py", "tools.json", "instructions.txt", "config.toml", "requirements.txt"):
        (ROOT / required).stat()
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    if config["luna"]["model"] != "gpt-5.6-luna-none" or "screen" in config:
        raise ValueError("One configured Luna and no screen watcher are required")


def remove(path):
    if not path.absolute().is_relative_to(ROOT) or path == ROOT:
        raise ValueError("Restoration escaped the source tree")
    attributes = path.lstat().st_file_attributes
    if attributes & 1024:
        (path.rmdir if attributes & 16 else path.unlink)()
    elif path.is_dir():
        for item in path.iterdir():
            remove(item)
        path.rmdir()
    else:
        if attributes & 1:
            path.chmod(stat.S_IWRITE)
        path.unlink()


def restore(archive, before, current):
    # Deepest first handles additions and file/directory replacements safely.
    for name in sorted(current, key=lambda value: len(Path(value).parts), reverse=True):
        if name not in before or current[name]["kind"] != before[name]["kind"] or current[name].get("links", 1) != 1:
            target = ROOT / name
            remove(target)
    with zipfile.ZipFile(archive) as saved:
        for name, entry in sorted(before.items(), key=lambda item: len(Path(item[0]).parts)):
            target = ROOT / name
            if entry["kind"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
            elif entry["kind"] == "file":
                if current.get(name) == entry:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.stat().st_file_attributes & 1:
                    target.chmod(stat.S_IWRITE)
                target.write_bytes(saved.read(name))
            else:
                raise ValueError("A repair baseline may not contain aliases")


def audit(record, visit, before, result):
    suffix = "after-" + uuid.uuid4().hex
    after_path = visit / (suffix + ".zip")
    after = snapshot(after_path)
    write(visit / (suffix + ".json"), after)
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    differences = {}
    with zipfile.ZipFile(visit / "before.zip") as old, zipfile.ZipFile(after_path) as new:
        for name in changed:
            left_entry, right_entry = before.get(name, {}), after.get(name, {})
            try:
                left = old.read(name).decode("utf-8") if left_entry.get("kind") == "file" else ""
                right = new.read(name).decode("utf-8") if right_entry.get("kind") == "file" else ""
                differences[name] = {"before": left_entry, "after": right_entry, "text": "".join(
                    difflib.unified_diff(left.splitlines(True), right.splitlines(True), fromfile="before/" + name, tofile="after/" + name))}
            except UnicodeDecodeError:
                differences[name] = {"before": left_entry, "after": right_entry, "binary_archives": True}
    record.append("repair_after", {"manifest": after, "archive": str(after_path),
                                   "changed": changed, "diff": differences, "report": result})
    return after, changed


async def main(folder):
    record = Record(folder)
    state = read(record.folder / "session.json")
    request = state["repair"]
    line, visit = None, None
    try:
        if not state.get("body_stopped"):
            raise RuntimeError("Repair requires confirmed body cleanup")
        visit = record.folder / request.setdefault("visit", "repair-" + uuid.uuid4().hex)
        if visit.parent != record.folder:
            raise ValueError("Invalid repair visit")
        visit.mkdir(exist_ok=True)
        save(record.folder, state)
        interrupted = request.get("phase") in ("editing", "auditing", "restoring")
        before = read(visit / "before.json")
        if before is None:
            if interrupted:
                raise RuntimeError("Interrupted repair lost its baseline; refuse to restart")
            validate()
            before = snapshot(visit / "before.zip")
            if any(entry["kind"] == "alias" or entry.get("links", 1) != 1 for entry in before.values()):
                raise ValueError("A repair baseline may not contain source/Git aliases")
            write(visit / "before.json", before)
            record.append("repair_before", {"request": request, "manifest": before, "archive": str(visit / "before.zip")})
        line = Line(asyncio.Queue(), record, state, calls=False)
        failure, result, changed = "Interrupted repair visit" if interrupted else None, None, []
        request["phase"] = "editing"
        save(record.folder, state)
        try:
            await line.open()
        except Exception as error:
            record.append("repair_transport_failure", {"error": str(error)})
        if not interrupted:
            instruction = (ROOT / "instructions.txt").read_text(encoding="utf-8") + (
                "\nThis is the repair visit. The live body is stopped. Edit general mechanisms in the real source tree. "
                "Use agent mode. Do not run Trident or create tests/harnesses. Preserve source isolation and saved runtime. "
                "Use assistant progress messages/tool calls; their actual events stream to Telegram. Give a factual final report."
            )
            try:
                result = await decision(record, instruction, {"repair": request, "current_task": state["task"],
                    "source": str(ROOT), "life": state["life"]}, ROOT)
            except BaseException as error:
                failure = f"{type(error).__name__}: {error}"
        request["phase"] = "auditing"
        save(record.folder, state)
        after, changed = audit(record, visit, before, result)
        try:
            if failure:
                raise RuntimeError(failure)
            if not changed:
                raise ValueError("Repair made no tree change")
            validate()
            record.append("repair_validated", {"changed": changed})
        except Exception as error:
            failure = str(error)
            request["phase"] = "restoring"
            save(record.folder, state)
            restore(visit / "before.zip", before, after)
            restored_path = visit / ("restored-" + uuid.uuid4().hex + ".zip")
            restored = snapshot(restored_path)
            if restored != before:
                raise RuntimeError("Restored tree differs from baseline")
            validate()
            record.append("repair_failed", {"error": failure, "restored_manifest": restored,
                                           "archive": str(restored_path)})
        state["repair"] = None
        state.update(attention=True, waiting=False)
        state["history"].append({"repair": {"changed": changed, "error": failure, "report": result}})
        save(record.folder, state)
    except BaseException as error:
        record.append("repair_recovery_required", {"error": f"{type(error).__name__}: {error}", "visit": str(visit)})
        save(record.folder, state)
        raise
    finally:
        if line is not None:
            record.append("repair_coordinator_closing", {"life": state["life"]})
            try:
                await line.close()
            except BaseException as error:
                record.append("repair_cleanup_failure", {"error": f"{type(error).__name__}: {error}"})
                raise
            record.append("repair_coordinator_closed", {"life": state["life"]})


if __name__ == "__main__":
    if os.environ.get("TRIDENT_LAUNCH") != str(Path(sys.argv[1]).resolve()) or sys.stdin.buffer.read() != b"start":
        raise RuntimeError("Start Trident through launch.py")
    asyncio.run(main(sys.argv[1]))
