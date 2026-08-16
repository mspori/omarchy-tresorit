import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr
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
        parsed = status.parse_status(
            "Tresorit daemon:\tstopped\nLogged in as:\t-\nRestriction state:\tNormal\n"
        )

        self.assertFalse(parsed["running"])
        self.assertFalse(parsed["authenticated"])
        self.assertEqual(parsed["statusText"], "Stopped")

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
        self.assertFalse(rows[1]["synced"])
        self.assertEqual(rows[1]["syncPath"], "")
        self.assertFalse(rows[1]["canStart"])

    def test_remembered_path_makes_stopped_tresor_restartable(self):
        rows = status.parse_tresors("Archive\t-\tOwner Two\n", {"Archive": "/old/path"})

        self.assertTrue(rows[0]["canStart"])

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


class StateTests(unittest.TestCase):
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


class CollectionTests(unittest.TestCase):
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
