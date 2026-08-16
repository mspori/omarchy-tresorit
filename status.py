#!/usr/bin/env python3
"""Read and control Tresorit through its supported command-line client."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence


COMMAND_TIMEOUT_SECONDS = 5
DEFAULT_CLI_PATH = Path.home() / ".local" / "share" / "tresorit" / "tresorit-cli"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_FILE = STATE_HOME / "omarchy" / "michaelspori.tresorit" / "sync-paths.json"
STATE_VERSION = 1
DISAMBIGUATED_NAME = re.compile(r"^(?P<name>.+) \((?P<id>[^()]+)\)$")


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


def tab_rows(raw: str, column_count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        columns = [value.strip() for value in line.split("\t")]
        if len(columns) == column_count:
            rows.append(columns)
    return rows


def parse_status(raw: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        label, separator, value = line.partition("\t")
        if separator:
            fields[label.rstrip(":").strip().lower()] = value.strip()

    if "tresorit daemon" not in fields or "logged in as" not in fields:
        raise ValueError("Tresorit status output is missing required fields")

    daemon_state = fields["tresorit daemon"]
    daemon_normalized = daemon_state.lower()
    if daemon_normalized not in ("running", "stopped", "not running", "unreachable"):
        raise ValueError("Tresorit status output contains an unknown daemon state")

    account = fields["logged in as"]
    restriction = fields.get("restriction state", "")
    if restriction == "-":
        restriction = ""
    drive_mount_path = fields.get("drive mount path", "")
    authenticated = account not in ("", "-")
    running = daemon_normalized == "running"

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
        "accountKey": account_key(account) if authenticated else "",
        "restrictionState": restriction,
        "driveMountPath": "" if drive_mount_path == "-" else drive_mount_path,
    }


def parse_tresors(
    raw: str, remembered_paths: dict[str, str] | None = None
) -> list[dict[str, object]]:
    remembered_paths = remembered_paths or {}
    source_rows = tab_rows(raw, 3)
    candidates = [DISAMBIGUATED_NAME.match(columns[0]) for columns in source_rows]
    candidate_counts: dict[str, int] = {}
    for candidate in candidates:
        if candidate:
            base_name = candidate.group("name")
            candidate_counts[base_name] = candidate_counts.get(base_name, 0) + 1

    tresors: list[dict[str, object]] = []
    for columns, candidate in zip(source_rows, candidates):
        raw_name, sync_path, owner = columns
        if not raw_name:
            continue
        is_duplicate = (
            candidate is not None
            and candidate_counts.get(candidate.group("name"), 0) > 1
        )
        name = candidate.group("name") if is_duplicate else raw_name
        identifier = candidate.group("id") if is_duplicate else raw_name
        synced = sync_path not in ("", "-")
        tresors.append(
            {
                "id": identifier,
                "name": name,
                "rawName": raw_name,
                "syncPath": sync_path if synced else "",
                "owner": owner if owner != "-" else "",
                "synced": synced,
                "status": "",
                "filesLeft": 0,
                "errors": 0,
                "canStart": synced or identifier in remembered_paths,
                "canStop": synced and remembered_paths.get(identifier) == sync_path,
            }
        )
    return tresors


def account_key(account: str) -> str:
    normalized = account.strip().casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def empty_state() -> dict[str, object]:
    return {"version": STATE_VERSION, "accounts": {}}


def prepare_state_directory() -> None:
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_FILE.parent.chmod(0o700)


@contextmanager
def state_lock():
    prepare_state_directory()
    lock_path = STATE_FILE.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def load_state() -> dict[str, object]:
    if STATE_FILE.is_symlink():
        return empty_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return empty_state()
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return empty_state()
    return data


def remembered_paths(state: dict[str, object], account: str) -> dict[str, str]:
    accounts = state.get("accounts", {})
    raw_paths = accounts.get(account_key(account), {}) if isinstance(accounts, dict) else {}
    if not isinstance(raw_paths, dict):
        return {}
    return {
        str(identifier): str(path)
        for identifier, path in raw_paths.items()
        if isinstance(identifier, str)
        and isinstance(path, str)
        and Path(path).is_absolute()
    }


def set_remembered_paths(state: dict[str, object], account: str, paths: dict[str, str]) -> None:
    accounts = state.setdefault("accounts", {})
    if not isinstance(accounts, dict):
        accounts = {}
        state["accounts"] = accounts
    accounts[account_key(account)] = paths


def save_state(state: dict[str, object]) -> None:
    prepare_state_directory()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".sync-paths.", suffix=".tmp", dir=STATE_FILE.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
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
        transfer = transfers.get(str(tresor["rawName"])) or transfers.get(str(tresor["name"]))
        if transfer:
            tresor.update(transfer)
        elif tresor["synced"]:
            tresor["status"] = "unknown"
    return tresors


def unavailable_status(message: str = "Tresorit CLI is not installed") -> dict[str, object]:
    return {
        "ok": True,
        "snapshotValid": True,
        "installed": False,
        "running": False,
        "authenticated": False,
        "statusText": message,
        "account": "",
        "accountKey": "",
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
        result["snapshotValid"] = False
        result["lastError"] = status_error or status_output or "Could not read Tresorit status"
        return result

    result = unavailable_status()
    try:
        result.update(parse_status(status_output))
    except ValueError as error:
        result["installed"] = True
        result["ok"] = False
        result["snapshotValid"] = False
        result["lastError"] = str(error)
        return result
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
        result["snapshotValid"] = False
        result["lastError"] = tresors_error or tresors_output or "Could not list tresors"
        return result

    account = str(result["account"])
    state_error = ""
    with state_lock():
        state = load_state()
        account_paths = remembered_paths(state, account)
        tresors = parse_tresors(tresors_output, account_paths)
        known_ids = {str(row["id"]) for row in tresors}
        next_paths = {
            identifier: path
            for identifier, path in account_paths.items()
            if identifier in known_ids
        }
        next_paths.update(
            {
                str(row["id"]): str(row["syncPath"])
                for row in tresors
                if row["synced"] and row["syncPath"]
            }
        )
        if next_paths != account_paths:
            set_remembered_paths(state, account, next_paths)
            try:
                save_state(state)
            except OSError as error:
                state_error = f"Could not safely remember sync folders: {error.strerror or error}"
        if not state_error:
            for row in tresors:
                identifier = str(row["id"])
                row["canStart"] = row["synced"] or identifier in next_paths
                row["canStop"] = row["synced"] and next_paths.get(identifier) == row["syncPath"]

    transfers = parse_transfers(transfers_output) if transfers_exit == 0 else {}
    result["tresors"] = merge_transfers(tresors, transfers)
    result["filesLeft"] = sum(int(row["filesLeft"]) for row in result["tresors"])
    result["errors"] = sum(int(row["errors"]) for row in result["tresors"])
    if state_error:
        result["ok"] = False
        result["lastError"] = state_error
    elif transfers_exit != 0:
        result["ok"] = False
        result["lastError"] = transfers_error or transfers_output or "Could not read transfers"
    return result


def valid_target(value: str) -> str:
    target = value.strip()
    if not target or "\0" in target or "\n" in target or "\r" in target:
        raise ValueError("Invalid tresor name or id")
    return target


def sync_context(
    cli: str,
) -> tuple[list[dict[str, object]], dict[str, str], str, str]:
    status_exit, status_output, status_error = run_cli(cli, ["-p", "status"])
    if status_exit != 0:
        raise ValueError(status_error or status_output or "Could not read Tresorit status")
    try:
        current = parse_status(status_output)
    except ValueError as error:
        raise ValueError(str(error)) from error
    if not current["running"] or not current["authenticated"]:
        raise ValueError("Tresorit must be running and authenticated")

    tresors_exit, tresors_output, tresors_error = run_cli(cli, ["-p", "tresors"])
    if tresors_exit != 0:
        raise ValueError(tresors_error or tresors_output or "Could not list tresors")

    account = str(current["account"])
    with state_lock():
        paths = remembered_paths(load_state(), account)
    return (
        parse_tresors(tresors_output, paths),
        paths,
        account,
        str(current["driveMountPath"]),
    )


def paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def validate_sync_path(
    raw_path: str,
    tresors: list[dict[str, object]],
    target_id: str,
    drive_mount_path: str,
    known_paths: dict[str, str] | None = None,
) -> Path:
    if not raw_path or "\0" in raw_path or "\n" in raw_path or "\r" in raw_path:
        raise ValueError("Invalid local sync folder")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("The local sync folder must be an absolute path")
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("The selected sync folder does not exist") from error
    if not candidate.is_dir():
        raise ValueError("The selected sync folder is not a directory")
    if candidate in (Path("/"), Path.home().resolve()):
        raise ValueError("Choose a dedicated folder, not the filesystem root or home folder")
    if not os.access(candidate, os.W_OK | os.X_OK):
        raise ValueError("The selected sync folder is not writable")

    if drive_mount_path:
        try:
            drive_path = Path(drive_mount_path).resolve(strict=False)
        except (OSError, RuntimeError):
            drive_path = Path(drive_mount_path)
        if paths_overlap(candidate, drive_path):
            raise ValueError("The sync folder cannot be inside Tresorit Drive")

    for tresor in tresors:
        if str(tresor["id"]) == target_id or not tresor["synced"]:
            continue
        existing_value = str(tresor["syncPath"])
        try:
            existing = Path(existing_value).resolve(strict=False)
        except (OSError, RuntimeError):
            existing = Path(existing_value)
        if paths_overlap(candidate, existing):
            raise ValueError("The sync folder cannot overlap another synced tresor")
    for identifier, existing_value in (known_paths or {}).items():
        if identifier == target_id:
            continue
        try:
            existing = Path(existing_value).resolve(strict=False)
        except (OSError, RuntimeError):
            existing = Path(existing_value)
        if paths_overlap(candidate, existing):
            raise ValueError("The sync folder cannot overlap another remembered tresor folder")
    return candidate


def remember_selected_path(account: str, target: str, path: Path) -> None:
    with state_lock():
        state = load_state()
        paths = remembered_paths(state, account)
        paths[target] = str(path)
        set_remembered_paths(state, account, paths)
        save_state(state)


def perform_action(
    cli: str,
    action: str,
    target: str | None,
    selected_path: str | None = None,
    expected_account_key: str | None = None,
) -> int:
    commands = {
        "start": ["start"],
        "stop": ["stop"],
    }
    if action in ("sync-start", "sync-start-at", "sync-stop"):
        if target is None:
            print("A tresor name or id is required", file=sys.stderr)
            return 2
        try:
            safe_target = valid_target(target)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        try:
            tresors, account_paths, account, drive_mount_path = sync_context(cli)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        if action == "sync-start-at" and account_key(account) != expected_account_key:
            print("The Tresorit account changed while choosing the sync folder", file=sys.stderr)
            return 2
        tresor = next((row for row in tresors if row["id"] == safe_target), None)
        if tresor is None:
            print("The requested tresor is not available", file=sys.stderr)
            return 2
        if action in ("sync-start", "sync-start-at"):
            if tresor["synced"]:
                print("The requested tresor is already synced", file=sys.stderr)
                return 2
            sync_path = account_paths.get(safe_target, "")
            if action == "sync-start-at":
                if selected_path is None:
                    print("A local sync folder is required", file=sys.stderr)
                    return 2
                try:
                    path = validate_sync_path(
                        selected_path,
                        tresors,
                        safe_target,
                        drive_mount_path,
                        account_paths,
                    )
                except ValueError as error:
                    print(error, file=sys.stderr)
                    return 2
                sync_path = str(path)
            if not sync_path:
                print(
                    "No usable previous sync folder is known; choose a local folder first",
                    file=sys.stderr,
                )
                return 2
            try:
                path = validate_sync_path(
                    sync_path,
                    tresors,
                    safe_target,
                    drive_mount_path,
                    account_paths,
                )
            except ValueError as error:
                print(error, file=sys.stderr)
                return 2
            sync_path = str(path)
            if action == "sync-start-at":
                try:
                    remember_selected_path(account, safe_target, path)
                except OSError as error:
                    print(
                        "The selected folder could not be safely remembered: "
                        + str(error),
                        file=sys.stderr,
                    )
                    return 2
            command = ["sync", "--start", safe_target, "--path", sync_path]
        else:
            sync_path = account_paths.get(safe_target, "")
            if not tresor["synced"] or not sync_path or sync_path != tresor["syncPath"]:
                print(
                    "Sync cannot be stopped until its current folder is safely remembered",
                    file=sys.stderr,
                )
                return 2
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
        choices=(
            "status",
            "start",
            "stop",
            "sync-start",
            "sync-start-at",
            "sync-stop",
        ),
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("path", nargs="?")
    parser.add_argument("account_key", nargs="?")
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
    return perform_action(
        cli,
        arguments.action,
        arguments.target,
        arguments.path,
        arguments.account_key,
    )


if __name__ == "__main__":
    raise SystemExit(main())
