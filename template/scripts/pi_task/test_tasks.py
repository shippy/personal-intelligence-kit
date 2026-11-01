import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from pi_task.native import native_policy
from pi_task.tasks import add_repositories, load_manifest, resolve_repository, task_directory, task_lock


class MultiRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state_root = self.root / "state"
        self.vault = self.root / "vault"
        self.para = self.root / "para"
        self.vault.mkdir()
        self.para.mkdir()
        self.repositories = []
        for name in ("presentations", "website", "resume"):
            repository = self.root / name
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text(name)
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture", "--no-gpg-sign"], check=True)
            self.repositories.append(repository)
        self.directory = task_directory(self.state_root, "my-talk")
        self.directory.mkdir(parents=True)
        self.manifest = {"version": 1, "name": "my-talk", "repositories": []}

    def test_create_and_expand_task_preserves_sessions_and_originals(self):
        add_repositories(self.directory, self.manifest, self.repositories[:2])
        sessions = self.directory / "sessions"
        sessions.mkdir()
        (sessions / "conversation.jsonl").write_text("existing conversation")
        loaded = load_manifest(self.directory, self.vault, self.para, self.state_root)
        add_repositories(self.directory, loaded, self.repositories[2:])
        self.assertEqual((sessions / "conversation.jsonl").read_text(), "existing conversation")
        final = load_manifest(self.directory, self.vault, self.para, self.state_root)
        roots = [Path(entry["worktree"]) for entry in final["repositories"]]
        policy = native_policy("project", roots[0], self.vault, self.para, self.state_root, self.root / "scratch", self.repositories, roots)
        self.assertEqual(policy["writable"], [str(root) for root in roots] + [str(self.root / "scratch")])
        for repository, root in zip(self.repositories, roots):
            self.assertNotIn(str(repository), policy["writable"])
            self.assertNotIn(str(repository), policy["denyWrite"])
            self.assertIn(str(root / ".git"), policy["denyWrite"])
            self.assertIn(str(root / ".env"), policy["denyRead"])
            self.assertEqual((repository / "README.md").read_text(), repository.name)
        self.assertIn(str(self.para), policy["denyWrite"])

    def test_collisions_preflight_all_repositories(self):
        subprocess.run(["git", "-C", str(self.repositories[1]), "branch", "pi/my-talk"], check=True)
        with self.assertRaises(ValueError):
            add_repositories(self.directory, self.manifest, self.repositories[:2])
        self.assertFalse((self.repositories[0] / ".pi/worktrees/my-talk").exists())

    def test_repo_alias_and_protected_paths(self):
        self.assertEqual(resolve_repository("resume", self.repositories, self.vault, self.para, self.state_root), self.repositories[2])
        with self.assertRaises(ValueError):
            resolve_repository(str(self.para), self.repositories, self.vault, self.para, self.state_root)

    def test_manifest_cannot_grant_original_checkout_write_access(self):
        add_repositories(self.directory, self.manifest, self.repositories[:1])
        self.manifest["repositories"][0]["worktree"] = str(self.repositories[0])
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            load_manifest(self.directory, self.vault, self.para, self.state_root)

    def test_task_lock_prevents_live_access_changes(self):
        with task_lock(self.directory):
            with self.assertRaises(ValueError):
                with task_lock(self.directory):
                    self.fail("Concurrent session acquired task lock")

    def test_task_name_cannot_escape_state(self):
        with self.assertRaises(ValueError):
            task_directory(self.state_root, "../../escape")


if __name__ == "__main__":
    unittest.main()
