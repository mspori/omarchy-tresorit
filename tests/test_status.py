import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "tresorit_status", Path(__file__).parents[1] / "status.py"
)
status = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(status)


class ParseStatusTests(unittest.TestCase):
    def test_logged_in_running_account(self):
        parsed = status.parse_status(
            "Tresorit daemon:\trunning\n"
            "Drive mount path:\t-\n"
            "Logged in as:\tperson@example.test\n"
            "Restriction state:\tNormal\n"
        )

        self.assertTrue(parsed["running"])
        self.assertTrue(parsed["authenticated"])
        self.assertEqual(parsed["statusText"], "Running")
        self.assertEqual(parsed["account"], "person@example.test")
        self.assertEqual(parsed["accountKey"], status.account_key("person@example.test"))
        self.assertEqual(parsed["driveMountPath"], "")

    def test_stopped_and_logged_out(self):
        for daemon_state in ("stopped", "not running", "unreachable"):
            with self.subTest(daemon_state=daemon_state):
                parsed = status.parse_status(
                    f"Tresorit daemon:\t{daemon_state}\n"
                    "Drive mount path:\t-\n"
                    "Logged in as:\t-\n"
                    "Restriction state:\t-\n"
                )

                self.assertFalse(parsed["running"])
                self.assertFalse(parsed["authenticated"])
                self.assertEqual(parsed["statusText"], "Stopped")
                self.assertEqual(parsed["restrictionState"], "")

    def test_restriction_is_the_visible_state(self):
        parsed = status.parse_status(
            "Tresorit daemon:\trunning\nLogged in as:\tperson@example.test\n"
            "Restriction state:\tRead only\n"
        )

        self.assertEqual(parsed["statusText"], "Read only")

    def test_missing_required_fields_are_rejected(self):
        for raw in ("", "Restriction state:\tNormal\n", "Tresorit daemon:\trunning\n"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    status.parse_status(raw)


class ParseTresorsTests(unittest.TestCase):
    def test_synced_and_unsynced_rows(self):
        rows = status.parse_tresors(
            "Projects\t/home/me/Tresorit/Projects\tOwner One\n"
            "Archive\t-\tOwner Two\n"
        )

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["synced"])
        self.assertEqual(rows[0]["syncPath"], "/home/me/Tresorit/Projects")
        self.assertEqual(rows[0]["linkedPath"], "/home/me/Tresorit/Projects")
        self.assertFalse(rows[1]["synced"])
        self.assertEqual(rows[1]["syncPath"], "")
        self.assertFalse(rows[1]["canStart"])

    def test_remembered_path_makes_stopped_tresor_restartable(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = status.parse_tresors(
                "Archive\t-\tOwner Two\n", {"Archive": directory}
            )

        self.assertTrue(rows[0]["canStart"])
        self.assertTrue(rows[0]["linkedPathUsable"])
        self.assertEqual(rows[0]["linkedPath"], directory)

    def test_duplicate_names_use_postfixed_ids(self):
        rows = status.parse_tresors(
            "Projects (alpha-id)\t/home/me/one\tOwner One\n"
            "Projects (beta-id)\t-\tOwner One\n"
            "Budget (2026)\t-\tOwner One\n"
        )

        self.assertEqual(rows[0]["id"], "alpha-id")
        self.assertEqual(rows[0]["name"], "Projects")
        self.assertEqual(rows[1]["id"], "beta-id")
        self.assertEqual(rows[2]["id"], "Budget (2026)")

    def test_duplicate_transfer_rows_merge_by_raw_name(self):
        tresors = status.parse_tresors(
            "Projects (alpha-id)\t/home/me/one\tOwner One\n"
            "Projects (beta-id)\t/home/me/two\tOwner One\n"
        )
        transfers = status.parse_transfers(
            "Projects (alpha-id)\tsyncing\t2\t0\n"
            "Projects (beta-id)\tidle\t0\t1\n"
        )

        merged = status.merge_transfers(tresors, transfers)
        self.assertEqual(merged[0]["filesLeft"], 2)
        self.assertEqual(merged[1]["errors"], 1)

    def test_transfer_data_is_merged_by_name(self):
        tresors = status.parse_tresors(
            "Projects\t/home/me/Tresorit/Projects\tOwner One\n"
            "Archive\t-\tOwner Two\n"
        )
        transfers = status.parse_transfers(
            "Projects\tsyncing\t7\t0\nArchive\tidle\t0\t2\n"
        )

        merged = status.merge_transfers(tresors, transfers)
        self.assertEqual(merged[0]["status"], "syncing")
        self.assertEqual(merged[0]["filesLeft"], 7)
        self.assertEqual(merged[1]["errors"], 2)

    def test_non_numeric_transfer_counts_are_safe(self):
        parsed = status.parse_transfers("Projects\tidle\t-\tunknown\n")

        self.assertEqual(parsed["Projects"]["filesLeft"], 0)
        self.assertEqual(parsed["Projects"]["errors"], 0)


class FileTransferTests(unittest.TestCase):
    def test_progress_percent_is_only_parsed_from_plain_valid_percentages(self):
        self.assertEqual(status.parse_progress_percent("42%"), 42)
        self.assertEqual(status.parse_progress_percent("42.5 %"), 42.5)
        self.assertEqual(status.parse_progress_percent("100.0%"), 100)
        for value in ("", "42", "101%", "-1%", "42% done"):
            with self.subTest(value=value):
                self.assertIsNone(status.parse_progress_percent(value))

    def test_file_rows_preserve_raw_status_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_file = root / "nested" / "report.txt"
            local_file.parent.mkdir()
            local_file.write_text("report", encoding="utf-8")
            tresors = status.parse_tresors(f"Projects\t{root}\tOwner\n")
            rows = status.parse_file_transfers(
                "Projects\tnested/report.txt\tdownloading\t37.5%\n", tresors
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "downloading")
        self.assertEqual(rows[0]["progress"], "37.5%")
        self.assertEqual(rows[0]["progressPercent"], 37.5)
        self.assertTrue(rows[0]["canOpen"])
        self.assertEqual(rows[0]["localPath"], str(local_file))

    def test_local_file_must_exist_inside_sync_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "sync"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "escape").symlink_to(outside)

            self.assertEqual(status.safe_local_file(str(root), "missing.txt"), "")
            self.assertEqual(status.safe_local_file(str(root), "../outside.txt"), "")
            self.assertEqual(status.safe_local_file(str(root), "escape"), "")
            self.assertEqual(status.safe_local_file(str(root), str(outside)), "")

    def test_duplicate_tresor_transfer_uses_disambiguated_raw_name(self):
        tresors = status.parse_tresors(
            "Projects (one)\t/one\tOwner\nProjects (two)\t/two\tOwner\n"
        )
        rows = status.parse_file_transfers(
            "Projects (two)\tfile.txt\tuploading\tunknown\n", tresors
        )

        self.assertEqual(rows[0]["tresorId"], "two")
        self.assertIsNone(rows[0]["progressPercent"])

    def test_ambiguous_duplicate_display_name_is_not_guessed(self):
        tresors = status.parse_tresors(
            "Projects (one)\t/one\tOwner\nProjects (two)\t/two\tOwner\n"
        )

        self.assertEqual(
            status.parse_file_transfers(
                "Projects\tfile.txt\tuploading\t50%\n", tresors
            ),
            [],
        )


class ActionValidationTests(unittest.TestCase):
    def test_valid_target_keeps_spaces(self):
        self.assertEqual(status.valid_target("Team Files"), "Team Files")

    def test_empty_or_multiline_target_is_rejected(self):
        for value in ("", "  ", "one\ntwo", "one\rtwo"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    status.valid_target(value)

    def test_sync_start_uses_scoped_remembered_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            row = {"id": "folder-id", "synced": False, "syncPath": ""}
            context = ([row], {"folder-id": str(path)}, "account", "")
            with mock.patch.object(status, "sync_context", return_value=context):
                with mock.patch.object(status, "run_cli", return_value=(0, "", "")) as run:
                    exit_code = status.perform_action("cli", "sync-start", "folder-id")

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(
            "cli", ["sync", "--start", "folder-id", "--path", str(path)]
        )

    def test_sync_stop_requires_durable_current_path(self):
        row = {"id": "folder-id", "synced": True, "syncPath": "/current"}
        with mock.patch.object(
            status, "sync_context", return_value=([row], {}, "account", "")
        ):
            errors = io.StringIO()
            with redirect_stderr(errors):
                exit_code = status.perform_action("cli", "sync-stop", "folder-id")

        self.assertEqual(exit_code, 2)
        self.assertIn("safely remembered", errors.getvalue())

    def test_sync_start_at_validates_remembers_and_uses_selected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "selected"
            selected.mkdir()
            state_file = root / "state" / "sync-paths.json"
            row = {"id": "folder-id", "synced": False, "syncPath": ""}
            context = ([row], {}, "account", "")
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(status, "sync_context", return_value=context):
                    with mock.patch.object(status, "run_cli", return_value=(0, "", "")) as run:
                        exit_code = status.perform_action(
                            "cli",
                            "sync-start-at",
                            "folder-id",
                            str(selected),
                            status.account_key("account"),
                        )
                with status.state_lock():
                    saved = status.remembered_paths(status.load_state(), "account")

        self.assertEqual(exit_code, 0)
        self.assertEqual(saved, {"folder-id": str(selected)})
        run.assert_called_once_with(
            "cli", ["sync", "--start", "folder-id", "--path", str(selected)]
        )

    def test_sync_start_at_rejects_overlapping_tresor_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            selected = existing / "nested"
            selected.mkdir(parents=True)
            rows = [
                {"id": "folder-id", "synced": False, "syncPath": ""},
                {"id": "other-id", "synced": True, "syncPath": str(existing)},
            ]

            with self.assertRaisesRegex(ValueError, "overlap"):
                status.validate_sync_path(selected.as_posix(), rows, "folder-id", "")

    def test_sync_start_at_rejects_tresorit_drive(self):
        with tempfile.TemporaryDirectory() as directory:
            drive = Path(directory) / "drive"
            selected = drive / "nested"
            selected.mkdir(parents=True)
            rows = [{"id": "folder-id", "synced": False, "syncPath": ""}]

            with self.assertRaisesRegex(ValueError, "Tresorit Drive"):
                status.validate_sync_path(
                    selected.as_posix(), rows, "folder-id", drive.as_posix()
                )

    def test_sync_start_at_rejects_account_change(self):
        row = {"id": "folder-id", "synced": False, "syncPath": ""}
        context = ([row], {}, "new-account", "")
        with mock.patch.object(status, "sync_context", return_value=context):
            errors = io.StringIO()
            with redirect_stderr(errors):
                exit_code = status.perform_action(
                    "cli", "sync-start-at", "folder-id", "/unused", "old-key"
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("account changed", errors.getvalue())

    def test_selected_path_cannot_overlap_stopped_remembered_tresor(self):
        with tempfile.TemporaryDirectory() as directory:
            remembered = Path(directory) / "remembered"
            selected = remembered / "nested"
            selected.mkdir(parents=True)
            rows = [
                {"id": "folder-id", "synced": False, "syncPath": ""},
                {"id": "stopped-id", "synced": False, "syncPath": ""},
            ]

            with self.assertRaisesRegex(ValueError, "remembered"):
                status.validate_sync_path(
                    selected.as_posix(),
                    rows,
                    "folder-id",
                    "",
                    {"stopped-id": remembered.as_posix()},
                )

    def test_failed_start_keeps_user_selected_path_for_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "selected"
            selected.mkdir()
            state_file = root / "state" / "sync-paths.json"
            row = {"id": "folder-id", "synced": False, "syncPath": ""}
            context = ([row], {}, "account", "")
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(status, "sync_context", return_value=context):
                    with mock.patch.object(
                        status, "run_cli", return_value=(1, "", "start failed")
                    ):
                        errors = io.StringIO()
                        with redirect_stderr(errors):
                            exit_code = status.perform_action(
                                "cli",
                                "sync-start-at",
                                "folder-id",
                                str(selected),
                                status.account_key("account"),
                            )
                with status.state_lock():
                    saved = status.remembered_paths(status.load_state(), "account")

        self.assertEqual(exit_code, 1)
        self.assertEqual(saved, {"folder-id": str(selected)})

    def test_sync_does_not_start_when_selected_path_cannot_be_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "selected"
            selected.mkdir()
            row = {"id": "folder-id", "synced": False, "syncPath": ""}
            context = ([row], {}, "account", "")
            with mock.patch.object(status, "sync_context", return_value=context):
                with mock.patch.object(
                    status, "remember_selected_path", side_effect=OSError("read only")
                ):
                    with mock.patch.object(status, "run_cli") as run:
                        errors = io.StringIO()
                        with redirect_stderr(errors):
                            exit_code = status.perform_action(
                                "cli",
                                "sync-start-at",
                                "folder-id",
                                str(selected),
                                status.account_key("account"),
                            )

        self.assertEqual(exit_code, 2)
        self.assertIn("safely remembered", errors.getvalue())
        run.assert_not_called()

    def test_sync_move_stops_remembers_and_starts_new_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_path = root / "old"
            new_path = root / "new"
            old_path.mkdir()
            new_path.mkdir()
            state_file = root / "state" / "sync-paths.json"
            row = {"id": "folder-id", "synced": True, "syncPath": str(old_path)}
            context = ([row], {"folder-id": str(old_path)}, "account", "")
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(status, "sync_context", return_value=context):
                    with mock.patch.object(
                        status,
                        "run_cli",
                        side_effect=[(0, "", ""), (0, "", "")],
                    ) as run:
                        exit_code = status.perform_action(
                            "cli",
                            "sync-move",
                            "folder-id",
                            str(new_path),
                            status.account_key("account"),
                        )
                with status.state_lock():
                    saved = status.remembered_paths(status.load_state(), "account")

        self.assertEqual(exit_code, 0)
        self.assertEqual(saved, {"folder-id": str(new_path)})
        self.assertEqual(
            run.call_args_list,
            [
                mock.call("cli", ["sync", "--stop", "folder-id"]),
                mock.call(
                    "cli",
                    ["sync", "--start", "folder-id", "--path", str(new_path)],
                ),
            ],
        )

    def test_failed_sync_move_restores_previous_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_path = root / "old"
            new_path = root / "new"
            old_path.mkdir()
            new_path.mkdir()
            state_file = root / "state" / "sync-paths.json"
            row = {"id": "folder-id", "synced": True, "syncPath": str(old_path)}
            context = ([row], {"folder-id": str(old_path)}, "account", "")
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(status, "sync_context", return_value=context):
                    with mock.patch.object(
                        status,
                        "run_cli",
                        side_effect=[
                            (0, "", ""),
                            (1, "", "new start failed"),
                            (0, "", ""),
                        ],
                    ) as run:
                        errors = io.StringIO()
                        with redirect_stderr(errors):
                            exit_code = status.perform_action(
                                "cli",
                                "sync-move",
                                "folder-id",
                                str(new_path),
                                status.account_key("account"),
                            )
                with status.state_lock():
                    saved = status.remembered_paths(status.load_state(), "account")

        self.assertEqual(exit_code, 1)
        self.assertEqual(saved, {"folder-id": str(old_path)})
        self.assertEqual(run.call_count, 3)
        self.assertIn("previous sync was restored", errors.getvalue())

    def test_timed_out_sync_move_does_not_start_competing_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_path = root / "old"
            new_path = root / "new"
            old_path.mkdir()
            new_path.mkdir()
            state_file = root / "state" / "sync-paths.json"
            row = {"id": "folder-id", "synced": True, "syncPath": str(old_path)}
            context = ([row], {"folder-id": str(old_path)}, "account", "")
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(status, "sync_context", return_value=context):
                    with mock.patch.object(
                        status,
                        "run_cli",
                        side_effect=[(0, "", ""), (124, "", "timed out")],
                    ) as run:
                        errors = io.StringIO()
                        with redirect_stderr(errors):
                            exit_code = status.perform_action(
                                "cli",
                                "sync-move",
                                "folder-id",
                                str(new_path),
                                status.account_key("account"),
                            )
                with status.state_lock():
                    saved = status.remembered_paths(status.load_state(), "account")

        self.assertEqual(exit_code, 124)
        self.assertEqual(saved, {"folder-id": str(new_path)})
        self.assertEqual(run.call_count, 2)
        self.assertIn("refresh status", errors.getvalue())


class StateTests(unittest.TestCase):
    def test_version_one_state_is_migrated_without_losing_paths(self):
        key = status.account_key("person@example.test")
        migrated = status.migrate_state(
            {"version": 1, "accounts": {key: {"folder-id": "/sync"}}}
        )

        self.assertEqual(migrated["version"], status.STATE_VERSION)
        self.assertEqual(
            status.remembered_paths(migrated, "person@example.test"),
            {"folder-id": "/sync"},
        )
        self.assertEqual(migrated["accounts"][key]["activeFiles"], {})
        self.assertEqual(migrated["accounts"][key]["completedFiles"], [])

    def test_paths_are_scoped_by_hashed_account(self):
        state = status.empty_state()
        status.set_remembered_paths(state, "one@example.test", {"id": "/one"})
        status.set_remembered_paths(state, "two@example.test", {"id": "/two"})

        self.assertEqual(
            status.remembered_paths(state, "one@example.test"), {"id": "/one"}
        )
        self.assertEqual(
            status.remembered_paths(state, "two@example.test"), {"id": "/two"}
        )
        self.assertNotIn("one@example.test", str(state))

    def test_state_round_trip_is_private_and_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "plugin" / "sync-paths.json"
            with mock.patch.object(status, "STATE_FILE", state_file):
                state = status.empty_state()
                status.set_remembered_paths(state, "person@example.test", {"id": "/sync"})
                with status.state_lock():
                    status.save_state(state)
                    loaded = status.load_state()

                self.assertEqual(
                    status.remembered_paths(loaded, "person@example.test"),
                    {"id": "/sync"},
                )
                self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(state_file.parent.stat().st_mode & 0o777, 0o700)


class FileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        self.tresor = {
            "id": "folder-id",
            "name": "Projects",
            "rawName": "Projects",
            "syncPath": "/sync/Projects",
            "synced": True,
            "errors": 0,
            "status": "idle",
        }

    def active_row(self, name="report.txt"):
        return status.file_row(self.tresor, name, "uploading", "25%")

    def test_disappearing_recent_healthy_file_is_recorded(self):
        state = status.empty_state()
        active = self.active_row()
        status.reconcile_file_history(state, "account", [self.tresor], [active], self.now)
        status.reconcile_file_history(
            state, "account", [self.tresor], [], self.now + timedelta(seconds=30)
        )

        stored = status.account_state(state, "account")["completedFiles"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["fileName"], "report.txt")
        self.assertEqual(stored[0]["completedAt"], "2026-08-16T12:00:30Z")

    def test_stale_error_and_unsynced_disappearances_are_not_completed(self):
        cases = (
            (self.now + timedelta(seconds=status.ACTIVE_FILE_STALE_SECONDS + 1), self.tresor),
            (self.now + timedelta(seconds=30), {**self.tresor, "errors": 1}),
            (self.now + timedelta(seconds=30), {**self.tresor, "synced": False}),
        )
        for later, tresor in cases:
            with self.subTest(later=later, errors=tresor["errors"], synced=tresor["synced"]):
                state = status.empty_state()
                status.reconcile_file_history(
                    state, "account", [self.tresor], [self.active_row()], self.now
                )
                status.reconcile_file_history(state, "account", [tresor], [], later)
                self.assertEqual(
                    status.account_state(state, "account")["completedFiles"], []
                )

    def test_unknown_or_missing_summary_status_does_not_complete_file(self):
        for summary_status in ("", "unknown"):
            with self.subTest(summary_status=summary_status):
                state = status.empty_state()
                status.reconcile_file_history(
                    state, "account", [self.tresor], [self.active_row()], self.now
                )
                status.reconcile_file_history(
                    state,
                    "account",
                    [{**self.tresor, "status": summary_status}],
                    [],
                    self.now + timedelta(seconds=30),
                )
                self.assertEqual(
                    status.account_state(state, "account")["completedFiles"], []
                )

    def test_active_last_seen_is_not_rewritten_inside_throttle_interval(self):
        state = status.empty_state()
        active = self.active_row()
        status.reconcile_file_history(state, "account", [self.tresor], [active], self.now)
        before = json.dumps(state, sort_keys=True)

        status.reconcile_file_history(
            state,
            "account",
            [self.tresor],
            [active],
            self.now + timedelta(seconds=status.ACTIVE_FILE_PERSIST_SECONDS - 1),
        )

        self.assertEqual(json.dumps(state, sort_keys=True), before)

    def test_currently_active_key_is_hidden_from_completed_snapshot(self):
        state = status.empty_state()
        active = self.active_row()
        bucket = status.account_state(state, "account", create=True)
        bucket["completedFiles"] = [
            {
                "key": active["key"],
                "tresorId": active["tresorId"],
                "tresorName": active["tresorName"],
                "fileName": active["fileName"],
                "completedAt": "2026-08-16T11:00:00Z",
            }
        ]

        rows = status.completed_file_rows(
            state, "account", [self.tresor], active_keys={str(active["key"])}
        )

        self.assertEqual(rows, [])

    def test_history_is_newest_first_deduplicated_and_capped(self):
        state = status.empty_state()
        for index in range(4):
            observed = self.now + timedelta(seconds=index * 20)
            active = self.active_row(f"file-{index}.txt")
            status.reconcile_file_history(
                state, "account", [self.tresor], [active], observed, history_limit=3
            )
            status.reconcile_file_history(
                state,
                "account",
                [self.tresor],
                [],
                observed + timedelta(seconds=5),
                history_limit=3,
            )

        stored = status.account_state(state, "account")["completedFiles"]
        self.assertEqual(
            [item["fileName"] for item in stored],
            ["file-3.txt", "file-2.txt", "file-1.txt"],
        )

    def test_completed_file_uses_usable_linked_path_after_sync_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_file = root / "report.txt"
            local_file.write_text("report", encoding="utf-8")
            state = status.empty_state()
            bucket = status.account_state(state, "account", create=True)
            bucket["completedFiles"] = [
                {
                    "key": status.file_key("folder-id", "report.txt"),
                    "tresorId": "folder-id",
                    "tresorName": "Projects",
                    "fileName": "report.txt",
                    "completedAt": "2026-08-16T12:00:00Z",
                }
            ]
            stopped = {
                **self.tresor,
                "syncPath": "",
                "synced": False,
                "linkedPath": str(root),
                "linkedPathUsable": True,
            }

            rows = status.completed_file_rows(state, "account", [stopped])

        self.assertTrue(rows[0]["canOpen"])
        self.assertEqual(rows[0]["localPath"], str(local_file))


class CollectionTests(unittest.TestCase):
    def test_successful_idle_poll_does_not_rewrite_unchanged_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state" / "sync-paths.json"
            account = "person@example.test"
            state = status.empty_state()
            status.set_remembered_paths(state, account, {"Projects": str(root)})
            outputs = {
                ("-p", "status"): (
                    0,
                    "Tresorit daemon:\trunning\n"
                    f"Logged in as:\t{account}\n"
                    "Restriction state:\tNormal",
                    "",
                ),
                ("-p", "tresors"): (0, f"Projects\t{root}\tOwner", ""),
                ("-p", "transfers"): (0, "Projects\tidle\t0\t0", ""),
                ("-p", "transfers", "--files"): (0, "", ""),
            }
            with mock.patch.object(status, "STATE_FILE", state_file):
                with status.state_lock():
                    status.save_state(state)
                with mock.patch.object(
                    status, "run_cli", side_effect=lambda _cli, args: outputs[tuple(args)]
                ):
                    with mock.patch.object(status, "save_state") as save:
                        result = status.collect_status("cli")

        self.assertTrue(result["ok"])
        save.assert_not_called()

    def test_collects_detailed_files_alongside_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_file = root / "report.txt"
            local_file.write_text("report", encoding="utf-8")
            outputs = {
                ("-p", "status"): (
                    0,
                    "Tresorit daemon:\trunning\n"
                    "Logged in as:\tperson@example.test\n"
                    "Restriction state:\tNormal",
                    "",
                ),
                ("-p", "tresors"): (0, f"Projects\t{root}\tOwner", ""),
                ("-p", "transfers"): (0, "Projects\tsyncing\t1\t0", ""),
                ("-p", "transfers", "--files"): (
                    0,
                    "Projects\treport.txt\tdownloading\t75%",
                    "",
                ),
            }
            state_file = root / "state" / "sync-paths.json"
            with mock.patch.object(status, "STATE_FILE", state_file):
                with mock.patch.object(
                    status, "run_cli", side_effect=lambda _cli, args: outputs[tuple(args)]
                ):
                    result = status.collect_status("cli", history_limit=10)

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["activeFiles"]), 1)
        self.assertEqual(result["activeFiles"][0]["progress"], "75%")
        self.assertEqual(result["activeFiles"][0]["progressPercent"], 75)
        self.assertEqual(result["activeFiles"][0]["localPath"], str(local_file))

    def test_file_poll_failure_does_not_reconcile_previous_active_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state" / "sync-paths.json"
            state = status.empty_state()
            tresor = {
                "id": "Projects",
                "name": "Projects",
                "rawName": "Projects",
                "syncPath": str(root),
                "synced": True,
                "errors": 0,
            }
            status.reconcile_file_history(
                state,
                "person@example.test",
                [tresor],
                [status.file_row(tresor, "report.txt")],
            )
            outputs = {
                ("-p", "status"): (
                    0,
                    "Tresorit daemon:\trunning\n"
                    "Logged in as:\tperson@example.test\n"
                    "Restriction state:\tNormal",
                    "",
                ),
                ("-p", "tresors"): (0, f"Projects\t{root}\tOwner", ""),
                ("-p", "transfers"): (0, "Projects\tidle\t0\t0", ""),
                ("-p", "transfers", "--files"): (1, "", "details unavailable"),
            }
            with mock.patch.object(status, "STATE_FILE", state_file):
                with status.state_lock():
                    status.save_state(state)
                with mock.patch.object(
                    status, "run_cli", side_effect=lambda _cli, args: outputs[tuple(args)]
                ):
                    result = status.collect_status("cli")
                with status.state_lock():
                    saved = status.load_state()

        bucket = status.account_state(saved, "person@example.test")
        self.assertFalse(result["ok"])
        self.assertTrue(result["snapshotValid"])
        self.assertIn("details unavailable", result["lastError"])
        self.assertEqual(len(bucket["activeFiles"]), 1)
        self.assertEqual(bucket["completedFiles"], [])

    def test_transfer_failure_is_degraded_not_healthy(self):
        outputs = {
            ("-p", "status"): (
                0,
                "Tresorit daemon:\trunning\n"
                "Logged in as:\tperson@example.test\n"
                "Restriction state:\tNormal",
                "",
            ),
            ("-p", "tresors"): (0, "Projects\t/sync/Projects\tOwner", ""),
            ("-p", "transfers"): (1, "Error code:\tUnavailable", ""),
            ("-p", "transfers", "--files"): (0, "", ""),
        }

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "plugin" / "sync-paths.json"
            with mock.patch.object(status, "STATE_FILE", state_file):
                read = lambda _cli, args: outputs[tuple(args)]
                with mock.patch.object(status, "run_cli", side_effect=read):
                    result = status.collect_status("cli")

        self.assertFalse(result["ok"])
        self.assertTrue(result["snapshotValid"])
        self.assertIn("Unavailable", result["lastError"])


if __name__ == "__main__":
    unittest.main()
