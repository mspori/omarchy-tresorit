import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
