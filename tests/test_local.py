import json
import unittest
from scripts.local_test import dotenv_text


class LocalConfigTests(unittest.TestCase):
    def test_config_round_trip_handles_windows_paths_without_losing_secrets(self):
        values = {'DISCORD_TOKEN': 'test-only-"quoted"', 'KLEE_DATA_DIR': r'C:\Users\user\Kleebot\data', 'CHAT_API_KEY': ''}
        # Quoted output uses the same backslash/quote escaping as JSON strings.
        parsed = {line.split('=', 1)[0]: json.loads(line.split('=', 1)[1]) for line in dotenv_text(values).splitlines()}
        self.assertEqual(parsed, values)

    def test_config_rejects_newline_injection(self):
        with self.assertRaises(ValueError):
            dotenv_text({'DISCORD_TOKEN': 'value\nOTHER=bad'})
