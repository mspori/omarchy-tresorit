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
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


COMMAND_TIMEOUT_SECONDS = 5
DEFAULT_CLI_PATH = Path.home() / ".local" / "share" / "tresorit" / "tresorit-cli"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
STATE_FILE = STATE_HOME / "omarchy" / "michaelspori.tresorit" / "sync-paths.json"
STATE_VERSION = 2
DEFAULT_FILE_HISTORY_LIMIT = 50
MIN_FILE_HISTORY_LIMIT = 10
MAX_FILE_HISTORY_LIMIT = 200
ACTIVE_FILE_STALE_SECONDS = 300
ACTIVE_FILE_PERSIST_SECONDS = 60
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


def interactive_login(cli: str) -> int:
    print("Tresorit CLI login")
    print("1) Email and password")
    print("2) Single sign-on (SSO)")
    try:
        method = input("Choose login method [1]: ").strip()
        if method in ("", "1"):
            email = input("Email: ").strip()
            if not email:
                print("An email address is required", file=sys.stderr)
                return 2
            command = [cli, "login", "--email", email, "--password-on-stdin"]
        elif method == "2":
            command = [cli, "login", "--sso"]
        else:
            print("Choose 1 or 2", file=sys.stderr)
            return 2
    except EOFError:
        print("Login cancelled", file=sys.stderr)
        return 2

    try:
        return subprocess.call(command)
    except OSError as error:
        print(error, file=sys.stderr)
        return 127


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
        linked_path = sync_path if synced else remembered_paths.get(identifier, "")
        linked_path_object = Path(linked_path) if linked_path else None
        linked_path_usable = bool(
            linked_path_object
            and linked_path_object.is_absolute()
            and linked_path_object.is_dir()
            and os.access(linked_path_object, os.W_OK | os.X_OK)
        )
        tresors.append(
            {
                "id": identifier,
                "name": name,
                "rawName": raw_name,
                "syncPath": sync_path if synced else "",
                "linkedPath": linked_path,
                "linkedPathUsable": linked_path_usable,
                "owner": owner if owner != "-" else "",
                "synced": synced,
                "status": "",
                "filesLeft": 0,
                "errors": 0,
                "canStart": synced or linked_path_usable,
                "canStop": synced and remembered_paths.get(identifier) == sync_path,
            }
        )
    return tresors


def account_key(account: str) -> str:
    normalized = account.strip().casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def empty_state() -> dict[str, object]:
    return {"version": STATE_VERSION, "accounts": {}}


def migrate_state(data: object) -> dict[str, object]:
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return empty_state()
    if data.get("version") == STATE_VERSION:
        return data
    if data.get("version") != 1:
        return empty_state()

    accounts: dict[str, object] = {}
    for key, paths in data["accounts"].items():
        if isinstance(key, str) and isinstance(paths, dict):
            accounts[key] = {
                "syncPaths": paths,
                "activeFiles": {},
                "completedFiles": [],
            }
    return {"version": STATE_VERSION, "accounts": accounts}


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
    return migrate_state(data)


def account_state(
    state: dict[str, object], account: str, create: bool = False
) -> dict[str, object]:
    accounts = state.get("accounts")
    if not isinstance(accounts, dict):
        if not create:
            return {}
        accounts = {}
        state["accounts"] = accounts
    key = account_key(account)
    value = accounts.get(key)
    if not isinstance(value, dict):
        if not create:
            return {}
        value = {"syncPaths": {}, "activeFiles": {}, "completedFiles": []}
        accounts[key] = value
    return value


def remembered_paths(state: dict[str, object], account: str) -> dict[str, str]:
    raw_paths = account_state(state, account).get("syncPaths", {})
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
    account_state(state, account, create=True)["syncPaths"] = paths


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
        temporary.chmod(0o600)
        os.replace(temporary, STATE_FILE)
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


def file_key(tresor_id: str, file_name: str) -> str:
    return hashlib.sha256(f"{tresor_id}\0{file_name}".encode("utf-8")).hexdigest()


def safe_local_file(sync_path: str, file_name: str) -> str:
    if not sync_path or not file_name or "\0" in file_name:
        return ""
    try:
        root = Path(sync_path).resolve(strict=True)
        supplied = Path(file_name)
        candidate = supplied if supplied.is_absolute() else root / supplied
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return ""
    if not root.is_dir() or not resolved.is_file() or not resolved.is_relative_to(root):
        return ""
    return str(resolved)


def tresor_for_transfer(
    tresors: list[dict[str, object]], transfer_name: str
) -> dict[str, object] | None:
    raw_matches = [row for row in tresors if str(row["rawName"]) == transfer_name]
    if len(raw_matches) == 1:
        return raw_matches[0]
    name_matches = [row for row in tresors if str(row["name"]) == transfer_name]
    return name_matches[0] if len(name_matches) == 1 else None


def file_row(
    tresor: dict[str, object], file_name: str, status_text: str = ""
) -> dict[str, object]:
    local_path = safe_local_file(str(tresor.get("syncPath", "")), file_name)
    return {
        "key": file_key(str(tresor["id"]), file_name),
        "tresorId": str(tresor["id"]),
        "tresorName": str(tresor["name"]),
        "fileName": file_name,
        "status": status_text,
        "localPath": local_path,
        "canOpen": bool(local_path),
    }


def parse_file_transfers(
    raw: str, tresors: list[dict[str, object]]
) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for transfer_name, file_name, status_text, _progress in tab_rows(raw, 4):
        tresor = tresor_for_transfer(tresors, transfer_name)
        if tresor is None or not file_name:
            continue
        files.append(file_row(tresor, file_name, status_text))
    return files


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def completed_file_rows(
    state: dict[str, object],
    account: str,
    tresors: list[dict[str, object]],
    history_limit: int = DEFAULT_FILE_HISTORY_LIMIT,
    active_keys: set[str] | None = None,
) -> list[dict[str, object]]:
    active_keys = active_keys or set()
    stored = account_state(state, account).get("completedFiles", [])
    if not isinstance(stored, list):
        return []
    by_id = {str(row["id"]): row for row in tresors}
    rows: list[dict[str, object]] = []
    for item in stored[:history_limit]:
        if not isinstance(item, dict):
            continue
        tresor_id = item.get("tresorId")
        file_name = item.get("fileName")
        completed_at = item.get("completedAt")
        if not all(isinstance(value, str) and value for value in (tresor_id, file_name, completed_at)):
            continue
        key = file_key(tresor_id, file_name)
        if key in active_keys:
            continue
        tresor = by_id.get(tresor_id)
        local_root = ""
        if tresor is not None:
            local_root = str(tresor.get("syncPath", ""))
            if not local_root and tresor.get("linkedPathUsable") is True:
                local_root = str(tresor.get("linkedPath", ""))
        local_path = (
            safe_local_file(local_root, file_name)
            if tresor is not None
            else ""
        )
        rows.append(
            {
                "key": key,
                "tresorId": tresor_id,
                "tresorName": str(item.get("tresorName", tresor_id)),
                "fileName": file_name,
                "completedAt": completed_at,
                "localPath": local_path,
                "canOpen": bool(local_path),
            }
        )
    return rows


def reconcile_file_history(
    state: dict[str, object],
    account: str,
    tresors: list[dict[str, object]],
    active_files: list[dict[str, object]],
    now: datetime | None = None,
    history_limit: int = DEFAULT_FILE_HISTORY_LIMIT,
) -> None:
    now = now or utc_now()
    bucket = account_state(state, account, create=True)
    previous = bucket.get("activeFiles", {})
    history = bucket.get("completedFiles", [])
    if not isinstance(previous, dict):
        previous = {}
    if not isinstance(history, list):
        history = []

    current_keys = {str(row["key"]) for row in active_files}
    tresors_by_id = {str(row["id"]): row for row in tresors}
    additions: list[dict[str, object]] = []
    for key, item in previous.items():
        if key in current_keys or not isinstance(item, dict):
            continue
        last_seen = parse_timestamp(item.get("lastSeenAt"))
        tresor_id = item.get("tresorId")
        file_name = item.get("fileName")
        tresor = tresors_by_id.get(tresor_id) if isinstance(tresor_id, str) else None
        age = (now - last_seen).total_seconds() if last_seen is not None else -1
        transfer_status = str(tresor.get("status", "")).strip().lower() if tresor else ""
        if (
            last_seen is None
            or age < 0
            or age > ACTIVE_FILE_STALE_SECONDS
            or tresor is None
            or tresor.get("synced") is not True
            or int(tresor.get("errors", 0)) != 0
            or transfer_status in ("", "unknown")
            or not isinstance(file_name, str)
            or not file_name
        ):
            continue
        additions.append(
            {
                "key": file_key(tresor_id, file_name),
                "tresorId": tresor_id,
                "tresorName": str(item.get("tresorName", tresor["name"])),
                "fileName": file_name,
                "completedAt": now.isoformat().replace("+00:00", "Z"),
            }
        )

    addition_keys = {str(item["key"]) for item in additions}
    next_history = additions + [
        item
        for item in history
        if isinstance(item, dict) and str(item.get("key", "")) not in addition_keys
    ]
    bucket["completedFiles"] = next_history[:history_limit]
    observed_at = now.isoformat().replace("+00:00", "Z")
    next_active: dict[str, object] = {}
    for row in active_files:
        key = str(row["key"])
        last_seen_at = observed_at
        old_item = previous.get(key)
        if isinstance(old_item, dict):
            old_seen = parse_timestamp(old_item.get("lastSeenAt"))
            old_age = (now - old_seen).total_seconds() if old_seen is not None else -1
            if 0 <= old_age < ACTIVE_FILE_PERSIST_SECONDS:
                last_seen_at = str(old_item["lastSeenAt"])
        next_active[key] = {
            "tresorId": str(row["tresorId"]),
            "tresorName": str(row["tresorName"]),
            "fileName": str(row["fileName"]),
            "lastSeenAt": last_seen_at,
        }
    bucket["activeFiles"] = next_active


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
        "activeFiles": [],
        "completedFiles": [],
        "filesLeft": 0,
        "errors": 0,
    }


def collect_status(
    cli: str, history_limit: int = DEFAULT_FILE_HISTORY_LIMIT
) -> dict[str, object]:
    history_limit = max(MIN_FILE_HISTORY_LIMIT, min(MAX_FILE_HISTORY_LIMIT, history_limit))
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
        if result["authenticated"]:
            with state_lock():
                result["completedFiles"] = completed_file_rows(
                    load_state(), str(result["account"]), [], history_limit
                )
        return result

    with ThreadPoolExecutor(max_workers=3) as executor:
        tresors_future = executor.submit(run_cli, cli, ["-p", "tresors"])
        transfers_future = executor.submit(run_cli, cli, ["-p", "transfers"])
        files_future = executor.submit(run_cli, cli, ["-p", "transfers", "--files"])
        tresors_exit, tresors_output, tresors_error = tresors_future.result()
        transfers_exit, transfers_output, transfers_error = transfers_future.result()
        files_exit, files_output, files_error = files_future.result()

    if tresors_exit != 0:
        result["ok"] = False
        result["snapshotValid"] = False
        result["lastError"] = tresors_error or tresors_output or "Could not list tresors"
        return result

    transfers = parse_transfers(transfers_output) if transfers_exit == 0 else {}
    account = str(result["account"])
    state_error = ""
    with state_lock():
        state = load_state()
        state_before = json.dumps(state, ensure_ascii=False, sort_keys=True)
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
        for row in tresors:
            identifier = str(row["id"])
            row["canStart"] = row["synced"] or row["linkedPathUsable"]
            row["canStop"] = row["synced"] and next_paths.get(identifier) == row["syncPath"]
        tresors = merge_transfers(tresors, transfers)
        active_files = parse_file_transfers(files_output, tresors) if files_exit == 0 else []
        if files_exit == 0 and transfers_exit == 0:
            reconcile_file_history(
                state, account, tresors, active_files, history_limit=history_limit
            )
        bucket = account_state(state, account, create=True)
        stored_history = bucket.get("completedFiles", [])
        if isinstance(stored_history, list) and len(stored_history) > history_limit:
            bucket["completedFiles"] = stored_history[:history_limit]
        state_changed = (
            json.dumps(state, ensure_ascii=False, sort_keys=True) != state_before
        )
        if state_changed:
            try:
                save_state(state)
            except OSError as error:
                state_error = f"Could not safely save Tresorit state: {error.strerror or error}"
                if next_paths != account_paths:
                    for row in tresors:
                        row["canStop"] = False
        completed_files = completed_file_rows(
            state,
            account,
            tresors,
            history_limit=history_limit,
            active_keys={str(row["key"]) for row in active_files},
        )

    result["tresors"] = tresors
    result["activeFiles"] = active_files
    result["completedFiles"] = completed_files
    result["filesLeft"] = sum(int(row["filesLeft"]) for row in result["tresors"])
    result["errors"] = sum(int(row["errors"]) for row in result["tresors"])
    if state_error:
        result["ok"] = False
        result["lastError"] = state_error
    elif transfers_exit != 0:
        result["ok"] = False
        result["lastError"] = transfers_error or transfers_output or "Could not read transfers"
    elif files_exit != 0:
        result["ok"] = False
        result["lastError"] = files_error or files_output or "Could not read file transfers"
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


def print_cli_result(stdout: str, stderr: str) -> None:
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)


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
    if action in ("sync-start", "sync-start-at", "sync-move", "sync-stop"):
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
        if action in ("sync-start-at", "sync-move") and account_key(account) != expected_account_key:
            print("The Tresorit account changed while choosing the sync folder", file=sys.stderr)
            return 2
        tresor = next((row for row in tresors if row["id"] == safe_target), None)
        if tresor is None:
            print("The requested tresor is not available", file=sys.stderr)
            return 2
        if action == "sync-move":
            old_path = account_paths.get(safe_target, "")
            if not tresor["synced"] or not old_path or old_path != tresor["syncPath"]:
                print(
                    "The current sync folder could not be safely verified",
                    file=sys.stderr,
                )
                return 2
            if selected_path is None:
                print("A new local sync folder is required", file=sys.stderr)
                return 2
            try:
                new_path = validate_sync_path(
                    selected_path,
                    tresors,
                    safe_target,
                    drive_mount_path,
                    account_paths,
                )
                old_path_resolved = Path(old_path).resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as error:
                print(error, file=sys.stderr)
                return 2
            if new_path == old_path_resolved:
                print("Choose a different folder for this tresor", file=sys.stderr)
                return 2

            stop_exit, stop_stdout, stop_stderr = run_cli(
                cli, ["sync", "--stop", safe_target]
            )
            print_cli_result(stop_stdout, stop_stderr)
            if stop_exit != 0:
                return stop_exit

            try:
                remember_selected_path(account, safe_target, new_path)
            except OSError as error:
                rollback_exit, rollback_stdout, rollback_stderr = run_cli(
                    cli, ["sync", "--start", safe_target, "--path", old_path]
                )
                print(
                    "Could not save the new folder; the previous sync "
                    + ("was restored" if rollback_exit == 0 else "also could not be restored")
                    + f": {error}",
                    file=sys.stderr,
                )
                print_cli_result(rollback_stdout, rollback_stderr)
                return 2

            start_exit, start_stdout, start_stderr = run_cli(
                cli, ["sync", "--start", safe_target, "--path", str(new_path)]
            )
            if start_exit == 0:
                print_cli_result(start_stdout, start_stderr)
                return 0
            if start_exit == 124:
                print(
                    "Starting sync in the new folder timed out; refresh status before retrying",
                    file=sys.stderr,
                )
                print_cli_result(start_stdout, start_stderr)
                return start_exit

            try:
                remember_selected_path(account, safe_target, old_path_resolved)
            except OSError as error:
                print(
                    "The new sync failed and the previous folder state could not be restored: "
                    + str(error),
                    file=sys.stderr,
                )
                print_cli_result(start_stdout, start_stderr)
                return start_exit
            rollback_exit, rollback_stdout, rollback_stderr = run_cli(
                cli, ["sync", "--start", safe_target, "--path", str(old_path_resolved)]
            )
            print(
                "Could not start sync in the new folder; the previous sync "
                + ("was restored" if rollback_exit == 0 else "also could not be restored"),
                file=sys.stderr,
            )
            print_cli_result(start_stdout, start_stderr)
            print_cli_result(rollback_stdout, rollback_stderr)
            return start_exit
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
    print_cli_result(stdout, stderr)
    return exit_code


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=(
            "status",
            "login",
            "start",
            "stop",
            "sync-start",
            "sync-start-at",
            "sync-move",
            "sync-stop",
        ),
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("path", nargs="?")
    parser.add_argument("account_key", nargs="?")
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_FILE_HISTORY_LIMIT,
        help="Maximum completed file entries to retain (10-200)",
    )
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
        print(
            json.dumps(
                collect_status(cli, arguments.history_limit), ensure_ascii=False
            )
        )
        return 0
    if arguments.action == "login":
        return interactive_login(cli)
    return perform_action(
        cli,
        arguments.action,
        arguments.target,
        arguments.path,
        arguments.account_key,
    )


if __name__ == "__main__":
    raise SystemExit(main())
