from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from dotenv import dotenv_values
from scripts.install_windows import create_config, prepare, shortcuts


class WindowsInstallTests(unittest.TestCase):
    def test_new_config_has_blank_keys_and_relative_data_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(create_config(root))
            values = dotenv_values(root / '.env.local')
            for key in ('DISCORD_TOKEN', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'NOVELAI_TOKEN', 'KLEE_GITHUB_ACTIONS_TOKEN'):
                self.assertEqual(values[key], '')
            self.assertEqual(values['KLEE_DATA_DIR'], 'data/local-test')
            self.assertEqual(values['KLEE_GITHUB_REPO'], 'M1zukiii/Kleebot')

    def test_existing_config_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b'# local only\r\nDISCORD_TOKEN=test-only\r\n'
            (root / '.env.local').write_bytes(original)
            self.assertFalse(create_config(root))
            self.assertEqual((root / '.env.local').read_bytes(), original)

    def fixture(self, root):
        (root / 'requirements.txt').write_text('example==1', encoding='utf-8')
        (root / 'klee_control.py').touch()
        executable = root / '.venv-local' / 'Scripts' / 'python.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        return executable

    def test_failed_dependency_install_never_marks_complete_or_rewrites_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / '.env.local').write_bytes(b'DISCORD_TOKEN=test-only')
            run = Mock(side_effect=subprocess.CalledProcessError(1, ['pip']))
            with self.assertRaises(subprocess.CalledProcessError):
                prepare(root, run)
            self.assertFalse((root / '.venv-local/klee-requirements.sha256').exists())
            self.assertEqual((root / '.env.local').read_bytes(), b'DISCORD_TOKEN=test-only')

    def test_failed_runtime_check_does_not_mark_dependencies_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            run = Mock(side_effect=[None, subprocess.CalledProcessError(1, ['ffmpeg'])])
            with self.assertRaises(subprocess.CalledProcessError):
                prepare(root, run)
            self.assertFalse((root / '.venv-local/klee-requirements.sha256').exists())

    def test_success_installs_full_dependencies_and_checks_ffmpeg(self):
        with tempfile.TemporaryDirectory(prefix='Klee space ') as directory:
            root = Path(directory)
            executable = self.fixture(root)
            run = Mock()
            python, created = prepare(root, run)
            self.assertEqual(python, executable)
            self.assertTrue(created)
            self.assertIn(str(root / 'requirements.txt'), run.call_args_list[0].args[0])
            self.assertIn('get_ffmpeg_exe', run.call_args_list[1].args[0][-1])
            self.assertTrue((root / '.venv-local/klee-requirements.sha256').exists())
            self.assertTrue((root / 'data/local-test/cache').is_dir())

    def test_shortcut_root_is_passed_as_data_not_shell_code(self):
        root = Path("C:/Klee space/$test'folder")
        run = Mock()
        shortcuts(root, run)
        args, kwargs = run.call_args
        self.assertNotIn(str(root), args[0][-1])
        self.assertEqual(kwargs['env']['KLEE_INSTALL_ROOT'], str(root))
        self.assertTrue(kwargs['check'])

    def test_incomplete_download_cannot_be_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                prepare(Path(directory), Mock())
