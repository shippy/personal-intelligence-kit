from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_task.cli import discover, session_state
from pi_task.native import native_policy, reference_repositories, SAFE_ENV


class NativePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.vault = self.root / "vault"
        self.para = self.root / "para"
        self.project = self.root / "project"
        for directory in (self.vault, self.para, self.project):
            directory.mkdir()

    def policy(self, profile, checkout):
        return native_policy(profile, checkout, self.vault, self.para, self.root / "state", self.root / "scratch")

    def test_para_launch_refused(self):
        with self.assertRaises(ValueError):
            discover(self.para, self.vault, self.para)

    def test_vault_subdirectory_selects_pkm(self):
        (self.vault / "working").mkdir()
        self.assertEqual(discover(self.vault / "working", self.vault, self.para), ("pkm", self.vault))

    def test_pkm_writes_only_output_working_logs_and_scratch(self):
        policy = self.policy("pkm", self.vault)
        self.assertEqual(policy["writable"], [str(self.vault / name) for name in ("output", "working", "logs")] + [str(self.root / "scratch")])
        self.assertIn(str(self.para), policy["denyWrite"])
        self.assertEqual(policy["network"]["mode"], "localhost")

    def test_project_keeps_ai_vault_read_only(self):
        policy = self.policy("project", self.project)
        self.assertIn(str(self.vault), policy["denyWrite"])
        self.assertIn(str(self.project), policy["writable"])

    def test_symlink_writable_root_rejected(self):
        (self.vault / "output").symlink_to(self.para, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.policy("pkm", self.vault)

    def test_secrets_and_control_environment_not_forwarded(self):
        for name in ("OPENAI_API_KEY", "FAL_AI", "HERDR_PANE_ID", "HERDR_SOCKET", "BASH_ENV", "NODE_OPTIONS", "PYTHONPATH"):
            self.assertNotIn(name, SAFE_ENV)

    def test_sessions_are_checkout_specific(self):
        self.assertNotEqual(session_state(self.root, self.project), session_state(self.root, self.vault))

    def test_pkm_can_read_references_but_not_write_them(self):
        policy = native_policy("pkm", self.vault, self.vault, self.para, self.root / "state", self.root / "scratch", [self.project])
        self.assertIn(str(self.project), policy["readable"])
        self.assertIn(str(self.project), policy["denyWrite"])
        self.assertNotIn(str(self.project), policy["writable"])
        self.assertIn(str(self.project / ".env"), policy["denyRead"])

    def test_active_reference_project_remains_writable(self):
        policy = native_policy("project", self.project, self.vault, self.para, self.root / "state", self.root / "scratch", [self.project])
        self.assertIn(str(self.project), policy["writable"])
        self.assertNotIn(str(self.project), policy["denyWrite"])

    def test_parent_reference_does_not_block_active_worktree(self):
        checkout = self.project / ".pi/worktrees/task"
        policy = native_policy("project", checkout, self.vault, self.para, self.root / "state", self.root / "scratch", [self.project])
        self.assertEqual(policy["writable"], [str(checkout), str(self.root / "scratch")])
        self.assertNotIn(str(self.project), policy["denyWrite"])

    def test_reference_list_defaults_to_empty(self):
        self.assertEqual(reference_repositories({}, self.vault, self.para), [])

    def test_reference_paths_resolve_relative_to_vault_and_deduplicate(self):
        with patch("pi_task.native.git", return_value=str(self.project)):
            self.assertEqual(reference_repositories({"pi_task": {"reference_repositories": ["../project", str(self.project)]}}, self.vault, self.para), [self.project])

    def test_invalid_reference_list_rejected(self):
        for value in ("../project", [None], [""], [str(self.para)], [str(self.root)]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reference_repositories({"pi_task": {"reference_repositories": value}}, self.vault, self.para)


if __name__ == "__main__":
    unittest.main()
