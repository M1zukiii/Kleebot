import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.maintenance import is_owner, request_update, update_status
from scripts.develop import allowed_path, apply_edits
from scripts.update import deploy_with_rollback


class MaintenanceTests(unittest.TestCase):
    def test_owner_is_explicit_and_defaults_to_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_owner(123))
        with patch.dict(os.environ, {"KLEE_OWNER_IDS": "123, 456"}, clear=True):
            self.assertTrue(is_owner(123))
            self.assertTrue(is_owner(456))
            self.assertFalse(is_owner(12))

    def test_update_request_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.assertIn("尚无", update_status(path))
            request_update(path)
            self.assertIn("requested_at", json.loads((path / "update_request.json").read_text()))
            (path / "update_status.json").write_text('{"status":"updated","revision":"1234567890123456"}')
            self.assertIn("123456789012", update_status(path))

    def test_generated_edits_reject_escape_secrets_and_controls(self):
        for name in ("../x.py", "/tmp/x.py", "bot/../main.py", "bot//main.py", "bot/./main.py", ".env", ".github/workflows/a.yml", "scripts/update.py", "bot/maintenance.py", "bot/.hidden.py", "bot\\main.py"):
            self.assertFalse(allowed_path(name), name)
        self.assertTrue(allowed_path("bot/chat_model.py"))
        self.assertTrue(allowed_path("tests/test_new.py"))

    def test_edit_is_exact_unique_and_syntax_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bot").mkdir()
            (root / "bot/a.py").write_text("x = 1\n")
            self.assertEqual(apply_edits(root, [{"path": "bot/a.py", "old": "x = 1", "new": "x = 2"}])["bot/a.py"], "x = 2\n")
            self.assertEqual((root / "bot/a.py").read_text(), "x = 1\n")  # no execution or source mutation
            with self.assertRaises(ValueError):
                apply_edits(root, [{"path": "bot/a.py", "old": "absent", "new": "pass"}])
            with self.assertRaises(SyntaxError):
                apply_edits(root, [{"path": "bot/new.py", "old": None, "new": "def broken("}])
            (root / "bot/link.py").symlink_to(root / "bot/a.py")
            with self.assertRaises(ValueError):
                apply_edits(root, [{"path": "bot/link.py", "old": "x = 1", "new": "pass"}])

    def test_successful_deploy_does_not_roll_back(self):
        writes, calls = [], []
        deploy_with_rollback(["compose"], "new", "old", writes.append, lambda args: calls.append(args))
        self.assertEqual(writes, ["new"])
        self.assertIn("--wait", calls[0])

    def test_unhealthy_deploy_restores_previous_image(self):
        writes, calls = [], []
        def execute(args):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("unhealthy")
        with self.assertRaises(RuntimeError):
            deploy_with_rollback(["compose"], "new", "old", writes.append, execute)
        self.assertEqual(writes, ["new", "old"])
        self.assertEqual(len(calls), 2)

    def test_failed_first_install_has_no_fabricated_rollback(self):
        writes = []
        def execute(args):
            raise RuntimeError("unhealthy")
        with self.assertRaises(RuntimeError):
            deploy_with_rollback(["compose"], "new", None, writes.append, execute)
        self.assertEqual(writes, ["new"])


class UpdaterFlowTests(unittest.TestCase):
    def simulate(self, root, fail_build=False, fail_deploy=False):
        import tarfile
        from types import SimpleNamespace
        import subprocess
        from scripts import update as updater
        calls = []
        deploy_count = 0
        def execute(args, **kwargs):
            nonlocal deploy_count
            calls.append(args)
            if args[:2] == ["git", "archive"]:
                target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--output="))
                with tarfile.open(target, 'w'):
                    pass
            if args[:2] == ["git", "rev-parse"]:
                return SimpleNamespace(stdout="a" * 40, returncode=0)
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(stdout="sha256:old", returncode=0)
            if fail_build and args[:2] == ["docker", "build"]:
                raise subprocess.CalledProcessError(1, args)
            if args[:2] == ["docker", "compose"] and "up" in args:
                deploy_count += 1
                if fail_deploy and deploy_count == 1:
                    raise subprocess.CalledProcessError(1, args)
            return SimpleNamespace(stdout="", returncode=0)
        with patch.object(updater, 'run', execute):
            if fail_build or fail_deploy:
                with self.assertRaises(subprocess.CalledProcessError), patch('builtins.print'):
                    updater.update(root, 'kleebot')
            else:
                updater.update(root, 'kleebot')
        return calls

    def test_build_failure_keeps_running_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = self.simulate(root, fail_build=True)
            self.assertFalse(any('stop' in call for call in calls))
            self.assertEqual(json.loads((root/'data/update_status.json').read_text())['status'], 'failed')

    def test_success_records_revision_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = self.simulate(root)
            self.assertEqual(json.loads((root/'.klee-updater/state.json').read_text())['revision'], 'a'*40)
            self.assertEqual(len(list((root/'.klee-updater/backups').glob('*.tar.gz'))), 1)
            checks = [call for call in calls if call[:2] == ['docker', 'run']]
            self.assertEqual(len(checks), 2)
            self.assertTrue(all('--network' in call and 'none' in call for call in checks))
            self.assertTrue(all('--env-file' not in call[:call.index('kleebot:rev-' + 'a'*40)] and '-v' not in call[:call.index('kleebot:rev-' + 'a'*40)] for call in checks))

    def test_bad_health_rolls_back_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = self.simulate(root, fail_deploy=True)
            override = json.loads((root/'.klee-updater/image.json').read_text())
            self.assertEqual(override['services']['kleebot']['image'], 'sha256:old')
            self.assertEqual(len([call for call in calls if call[:2] == ['docker', 'compose'] and 'up' in call]), 2)
            self.assertEqual(json.loads((root/'.klee-updater/state.json').read_text())['failed_revision'], 'a'*40)
