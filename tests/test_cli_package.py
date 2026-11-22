"""CLI/public packaging regression tests, with no Garmin or OpenAI calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class InstalledInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'private_home'
        self.env = dict(os.environ, RECOVERY_ADVISOR_HOME=str(self.home))
        # Source-tree tests run before optional pip installation; resolve package from project.
        self.env['PYTHONPATH'] = str(PROJECT) + os.pathsep + os.getenv('PYTHONPATH', '')

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, '-m', 'recovery_advisor', *args],
            cwd=self.temp.name, env=self.env,
            capture_output=True, text=True, timeout=30,
        )

    def test_all_expected_subcommands_are_available(self):
        result = self.invoke('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ('sync','report','ask','feedback','status','activities','classify'):
            self.assertIn(command, result.stdout)

    def test_cli_version(self):
        result = self.invoke('--version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2.3.0', result.stdout)

    def test_command_works_outside_project_and_preserves_settings(self):
        result = self.invoke('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / 'settings.json').exists())
        self.assertTrue((self.home / 'data' / 'advisor.sqlite3').exists())
        result = self.invoke('set', 'weight_kg', '70')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.invoke('set', 'race_goal', 'Ironman 70.3')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.home / 'settings.json').read_text(encoding='utf8'))
        self.assertEqual(config['weight_kg'], 70)
        self.assertEqual(config['race_goal'], 'Ironman 70.3')
        self.assertNotIn('notes_only_activity_ids', config)

    def test_offline_import_and_feedback_work_in_isolated_home(self):
        example = PROJECT / 'examples' / 'sample_activities.json'
        result = self.invoke('--as-of', '2026-11-08', 'import-json', str(example))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"added": 4', result.stdout)
        result = self.invoke('--as-of', '2026-11-08', 'feedback',
                             '--question', 'Legs felt unusually heavy')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.invoke('--as-of', '2026-11-08', 'report')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = (self.home / 'reports' / '2026-11-08_recovery.md').read_text(encoding='utf8')
        self.assertIn('Legs felt unusually heavy', report)
        self.assertIn('Experimental recovery context score', report)

    def test_compatibility_module_is_small_and_exposes_original_api(self):
        import advisor
        self.assertLess(len((PROJECT / 'advisor.py').read_text().splitlines()), 70)
        self.assertTrue(callable(advisor.sync_with_client))
        self.assertTrue(callable(advisor.context_data))


if __name__ == '__main__':
    unittest.main()
