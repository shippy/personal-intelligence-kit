from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_task.handoff import transition_args, validate_approval
from pi_task import native


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.request = {"action": "create", "task": "my-talk", "repositories": ["/projects/presentations"], "brief": "Agreed spec"}

    def test_approval_cannot_expand_eligible_paths(self):
        with self.assertRaises(ValueError):
            validate_approval(self.request, [], None)

    def test_add_is_bound_to_current_task(self):
        with self.assertRaises(ValueError):
            validate_approval({**self.request, "action": "add"}, self.request["repositories"], "different-task")

    def test_create_transition_uses_structured_arguments(self):
        args = transition_args(Namespace(vault=Path("/vault")), self.request, Path("/private/brief.md"))
        self.assertEqual(args.new, "my-talk")
        self.assertEqual(args.repo, ["/projects/presentations"])
        self.assertFalse(args.resume)
        self.assertIsNone(args.prompt)
        self.assertEqual(args.handoff, Path("/private/brief.md"))

    def test_add_transition_preserves_task_identity(self):
        args = transition_args(Namespace(vault=Path("/vault")), {**self.request, "action": "add"}, Path("/private/brief.md"))
        self.assertEqual(args.task, "my-talk")
        self.assertEqual(args.add_repo, ["/projects/presentations"])
        self.assertIsNone(args.new)

    def test_launcher_releases_source_before_transition(self):
        events = []
        brief_paths = []

        def run_session(args, stack):
            if not events:
                events.append("source")
                stack.callback(events.append, "released")
                return self.request
            self.assertEqual(events, ["source", "released"])
            self.assertEqual(args.new, "my-talk")
            self.assertEqual(args.handoff.read_text(), "Agreed spec")
            brief_paths.append(args.handoff)
            events.append("destination")

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".local/state/pi-task").mkdir(parents=True)
            with patch.object(native, "parser") as parser, patch.object(native, "run", side_effect=run_session), patch.object(native.Path, "home", return_value=home):
                parser.return_value.parse_args.return_value = Namespace(vault=Path("/vault"))
                native.main()
            self.assertEqual(events, ["source", "released", "destination"])
            self.assertFalse(brief_paths[0].exists())


if __name__ == "__main__":
    unittest.main()
