#!/usr/bin/env python3
"""Read and control Tresorit through its supported command-line client."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence


COMMAND_TIMEOUT_SECONDS = 5
DEFAULT_CLI_PATH = Path.home() / ".local" / "share" / "tresorit" / "tresorit-cli"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_FILE = STATE_HOME / "omarchy" / "michaelspori.tresorit" / "sync-paths.json"


def find_cli() -> str | None:
    override = os.environ.get("TRESORIT_CLI", "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None

    on_path = shutil.which("tresorit-cli")
    if on_path:
        return on_path
    if DEFAULT_CLI_PATH.is_file() and os.access(DEFAULT_CLI_PATH, os.X_OK):
        return str(DEFAULT_CLI_PATH)
    return None


def run_cli(cli: str, arguments: Sequence[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            [cli, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "Tresorit CLI timed out"
    except OSError as error:
        return 127, "", str(error)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def tab_rows(raw: str, minimum_columns: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        columns = [value.strip() for value in line.split("\t")]
        if len(columns) >= minimum_columns:
            rows.append(columns)
    return rows


def parse_status(raw: str) -> dict[str, object]:
    fields = {
        columns[0].rstrip(":").strip().lower(): columns[1].strip()
        for columns in tab_rows(raw, 2)
    }
    daemon_state = fields.get("tresorit daemon", "unknown")
    account = fields.get("logged in as", "")
    restriction = fields.get("restriction state", "")
    drive_mount_path = fields.get("drive mount path", "")
    authenticated = account not in ("", "-")
    running = daemon_state.lower() == "running"

    if not running:
        status_text = "Stopped"
    elif not authenticated:
        status_text = "Login required"
    elif restriction and restriction.lower() != "normal":
        status_text = restriction
    else:
        status_text = "Running"

    return {
        "running": running,
        "authenticated": authenticated,
        "statusText": status_text,
        "account": account if authenticated else "",
        "restrictionState": restriction,
        "driveMountPath": "" if drive_mount_path == "-" else drive_mount_path,
    }


def parse_tresors(
    raw: str, remembered_paths: dict[str, str] | None = None
) -> list[dict[str, object]]:
    remembered_paths = remembered_paths or {}
    tresors: list[dict[str, object]] = []
    for columns in tab_rows(raw, 3):
        name, sync_path, owner = columns[:3]
        if not name:
            continue
        synced = sync_path not in ("", "-")
        tresors.append(
            {
                "id": name,
                "name": name,
                "syncPath": sync_path if synced else "",
                "owner": owner if owner != "-" else "",
                "synced": synced,
                "status": "",
                "filesLeft": 0,
                "errors": 0,
                "canStart": synced or name in remembered_paths,
            }
        )
    return tresors


def load_remembered_paths() -> dict[str, str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and Path(value).is_absolute()
    }


def save_remembered_paths(paths: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(paths, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, STATE_FILE)
        STATE_FILE.chmod(0o600)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def parse_transfers(raw: str) -> dict[str, dict[str, object]]:
    transfers: dict[str, dict[str, object]] = {}
    for columns in tab_rows(raw, 4):
        name, status, files_left, errors = columns[:4]
        try:
            file_count = int(files_left)
        except ValueError:
            file_count = 0
        try:
            error_count = int(errors)
        except ValueError:
            error_count = 0
        transfers[name] = {
            "status": status,
            "filesLeft": max(0, file_count),
            "errors": max(0, error_count),
        }
    return transfers


def merge_transfers(
    tresors: list[dict[str, object]], transfers: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    for tresor in tresors:
        transfer = transfers.get(str(tresor["name"]))
        if transfer:
            tresor.update(transfer)
        elif tresor["synced"]:
            tresor["status"] = "unknown"
    return tresors


def unavailable_status(message: str = "Tresorit CLI is not installed") -> dict[str, object]:
    return {
        "ok": True,
        "installed": False,
        "running": False,
        "authenticated": False,
        "statusText": message,
        "account": "",
        "restrictionState": "",
        "driveMountPath": "",
        "tresors": [],
        "filesLeft": 0,
        "errors": 0,
    }


def collect_status(cli: str) -> dict[str, object]:
    status_exit, status_output, status_error = run_cli(cli, ["-p", "status"])
    if status_exit != 0:
        result = unavailable_status("Tresorit is unavailable")
        result["installed"] = True
        result["ok"] = False
        result["lastError"] = status_error or status_output or "Could not read Tresorit status"
        return result

    result = unavailable_status()
    result.update(parse_status(status_output))
    result["installed"] = True

    if not result["running"] or not result["authenticated"]:
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        tresors_future = executor.submit(run_cli, cli, ["-p", "tresors"])
        transfers_future = executor.submit(run_cli, cli, ["-p", "transfers"])
        tresors_exit, tresors_output, tresors_error = tresors_future.result()
        transfers_exit, transfers_output, transfers_error = transfers_future.result()

    if tresors_exit != 0:
        result["ok"] = False
        result["lastError"] = tresors_error or tresors_output or "Could not list tresors"
        return result

    remembered_paths = load_remembered_paths()
    tresors = parse_tresors(tresors_output, remembered_paths)
    observed_paths = {
        str(row["id"]): str(row["syncPath"])
        for row in tresors
        if row["synced"] and row["syncPath"]
    }
    next_paths = {**remembered_paths, **observed_paths}
    if next_paths != remembered_paths:
        try:
            save_remembered_paths(next_paths)
        except OSError:
            pass
    transfers = parse_transfers(transfers_output) if transfers_exit == 0 else {}
    result["tresors"] = merge_transfers(tresors, transfers)
    result["filesLeft"] = sum(int(row["filesLeft"]) for row in result["tresors"])
    result["errors"] = sum(int(row["errors"]) for row in result["tresors"])
    if transfers_exit != 0:
        result["lastError"] = transfers_error or transfers_output or "Could not read transfers"
    return result


def valid_target(value: str) -> str:
    target = value.strip()
    if not target or "\0" in target or "\n" in target or "\r" in target:
        raise ValueError("Invalid tresor name or id")
    return target


def perform_action(cli: str, action: str, target: str | None) -> int:
    commands = {
        "start": ["start"],
        "stop": ["stop"],
    }
    if action in ("sync-start", "sync-stop"):
        if target is None:
            print("A tresor name or id is required", file=sys.stderr)
            return 2
        try:
            safe_target = valid_target(target)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        if action == "sync-start":
            sync_path = load_remembered_paths().get(safe_target, "")
            path = Path(sync_path)
            if not sync_path or not path.is_absolute() or not path.is_dir() or not os.access(path, os.W_OK):
                print(
                    "No usable previous sync folder is known; choose a folder in the Tresorit app",
                    file=sys.stderr,
                )
                return 2
            command = ["sync", "--start", safe_target, "--path", sync_path]
        else:
            command = ["sync", "--stop", safe_target]
    else:
        command = commands[action]

    exit_code, stdout, stderr = run_cli(cli, command)
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    return exit_code


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "start", "stop", "sync-start", "sync-stop"),
    )
    parser.add_argument("target", nargs="?")
    return parser


def main() -> int:
    arguments = argument_parser().parse_args()
    cli = find_cli()
    if cli is None:
        if arguments.action == "status":
            print(json.dumps(unavailable_status()))
            return 0
        print("Tresorit CLI is not installed", file=sys.stderr)
        return 127

    if arguments.action == "status":
        print(json.dumps(collect_status(cli), ensure_ascii=False))
        return 0
    return perform_action(cli, arguments.action, arguments.target)


if __name__ == "__main__":
    raise SystemExit(main())
