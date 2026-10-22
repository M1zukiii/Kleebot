import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
import zipfile

from scripts.local_supervisor import LocalSupervisor, build_environment, extract_source


class LocalUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runner = LocalSupervisor(self.root, {})
        self.old, self.new = 'a' * 40, 'b' * 40
        self.runner.active = self.old
        self.runner.state = {'revision': self.old}
        self.runner.latest = Mock(return_value=self.new)
        self.runner.prepare = Mock()
        self.runner.stop = Mock()
        self.runner.start = Mock(side_effect=lambda revision: setattr(self.runner, 'active', revision))
        self.runner.wait_ready = Mock(return_value=True)

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        return json.loads((self.runner.data / 'update_status.json').read_text(encoding='utf-8'))

    def test_preparation_failure_keeps_live_bot_and_state(self):
        self.runner.prepare.side_effect = RuntimeError('test failure')
        self.runner.update()
        self.runner.stop.assert_not_called()
        self.runner.start.assert_not_called()
        self.assertEqual(self.runner.state['revision'], self.old)
        self.assertEqual(self.status()['status'], 'failed')

    def test_success_backs_up_memory_and_preserves_config(self):
        (self.root / '.env.local').write_text('DISCORD_TOKEN=private', encoding='utf-8')
        (self.runner.data / 'conversations.sqlite3').write_bytes(b'original memory')
        self.runner.update()
        self.runner.start.assert_called_once_with(self.new)
        self.assertEqual(self.runner.state['revision'], self.new)
        self.assertEqual(self.runner.state['previous'], self.old)
        backups = list((self.runner.private / 'backups').glob('*/conversations.sqlite3'))
        self.assertEqual(backups[0].read_bytes(), b'original memory')
        self.assertEqual((self.root / '.env.local').read_text(), 'DISCORD_TOKEN=private')
        self.assertEqual(self.status()['status'], 'updated')

    def test_failed_ready_rolls_back_and_does_not_record_bad_revision(self):
        self.runner.wait_ready.side_effect = [False, True]
        self.runner.update()
        self.assertEqual([call.args[0] for call in self.runner.start.call_args_list], [self.new, self.old])
        self.assertEqual(self.runner.active, self.old)
        self.assertEqual(self.runner.state['revision'], self.old)
        self.assertEqual(self.status()['status'], 'failed')

    def test_failed_rollback_is_not_claimed_as_running(self):
        self.runner.wait_ready.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Rollback failed'):
            self.runner.update()
        self.assertEqual(self.status()['status'], 'rollback_failed')

    def test_same_revision_skips_preparation_and_restart(self):
        self.runner.latest.return_value = self.old
        self.runner.update()
        self.runner.prepare.assert_not_called()
        self.runner.stop.assert_not_called()
        self.assertEqual(self.status()['status'], 'current')

    def test_only_fresh_instance_heartbeat_counts(self):
        self.runner.process = SimpleNamespace(poll=lambda: None)
        self.runner.ready = self.root / 'ready'
        self.runner.started = 100
        with patch('scripts.local_supervisor.time.time', return_value=110), \
             patch('scripts.local_supervisor.time.monotonic', side_effect=[0, 0, 2]), \
             patch('scripts.local_supervisor.time.sleep'):
            self.runner.ready.write_text('99')
            self.assertFalse(LocalSupervisor.wait_ready(self.runner, timeout=1))
        with patch('scripts.local_supervisor.time.time', return_value=110):
            self.runner.ready.write_text('105')
            self.assertTrue(LocalSupervisor.wait_ready(self.runner, timeout=1))

    def test_source_archive_cannot_escape_or_link(self):
        archive = self.root / 'source.zip'
        for name in ('root/../../escape.py', '/absolute', 'root/C:/escape', 'root\\escape'):
            with zipfile.ZipFile(archive, 'w') as source:
                info = zipfile.ZipInfo()
                info.filename = name
                source.writestr(info, 'bad')
            with self.assertRaises(ValueError, msg=name):
                extract_source(archive, self.root / 'extract')
        with zipfile.ZipFile(archive, 'w') as source:
            info = zipfile.ZipInfo('root/link')
            info.external_attr = 0o120777 << 16
            source.writestr(info, '../outside')
        with self.assertRaises(ValueError):
            extract_source(archive, self.root / 'extract')

    def test_build_environment_excludes_keys(self):
        with patch.dict(os.environ, {'CHAT_API_KEY': 'secret', 'DISCORD_TOKEN': 'secret', 'PATH': 'bin'}, clear=True):
            self.assertEqual(build_environment(), {'PATH': 'bin'})

    def test_real_child_bootstrap_uses_shared_data_and_instance_heartbeat(self):
        runner = LocalSupervisor(self.root, {})
        (self.root / 'bot').mkdir()
        (self.root / 'bot/__init__.py').write_text('')
        (self.root / 'dotenv.py').write_text('def load_dotenv(*args, **kwargs): pass\n')
        (self.root / 'bot/main.py').write_text(
            'import os,time\nfrom pathlib import Path\n'
            "assert os.environ['KLEE_LOCAL_SUPERVISED'] == '1'\n"
            "assert Path(os.environ['KLEE_DATA_DIR']).is_absolute()\n"
            "Path(os.environ['KLEE_READY_FILE']).write_text(str(time.time()))\n"
            "print('test child ready', flush=True)\n"
            'time.sleep(3600)\n', encoding='utf-8')
        runner.source = Mock(return_value=(self.root, Path(sys.executable)))
        try:
            runner.start(None)
            self.assertTrue(runner.wait_ready(timeout=10))
            self.assertTrue(runner.ready.is_file())
        finally:
            runner.stop()
        self.assertIsNotNone(runner.process.poll())
        self.assertFalse(runner.ready.exists())

    def test_public_main_without_framework_is_rejected_before_test_execution(self):
        # Exercise real download/extraction up to the compatibility gate.
        import io
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as source:
            source.writestr('Kleebot/bot/main.py', 'pass')
        archive.seek(0)
        with patch('scripts.local_supervisor.urlopen', return_value=archive), \
             patch('scripts.local_supervisor.venv.EnvBuilder') as builder:
            with self.assertRaisesRegex(ValueError, 'main'):
                LocalSupervisor.prepare(self.runner, self.new)
            builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
