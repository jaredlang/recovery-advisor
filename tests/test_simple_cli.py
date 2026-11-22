"""Task-oriented CLI regression tests; no live Garmin/OpenAI calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from recovery_advisor import cli


class SimplifiedInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'private'
        self.env = dict(os.environ, RECOVERY_ADVISOR_HOME=str(self.home))
        self.env['PYTHONPATH'] = str(PROJECT) + os.pathsep + os.environ.get('PYTHONPATH', '')

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self,*args):
        return subprocess.run([sys.executable,'-m','recovery_advisor',*args],
                              cwd=self.temp.name,env=self.env,
                              capture_output=True,text=True,timeout=30)

    def test_top_level_help_shows_only_frequent_tasks(self):
        result=self.invoke('--help')
        self.assertEqual(result.returncode,0,result.stderr)
        for item in ('sync','review','ask','status','config','manage'):
            self.assertIn('recovery-advisor '+item,result.stdout)
        for old in ('feedback-list','link-feedback','review-activities','mark-feedback'):
            self.assertNotIn(old,result.stdout)
        self.assertEqual(self.invoke().returncode,0)

    def test_config_display_and_short_values(self):
        self.assertEqual(self.invoke('config','weight','70').returncode,0)
        self.assertEqual(self.invoke('config','hr','50').returncode,0)
        self.assertEqual(self.invoke('config','goal','Ironman 70.3').returncode,0)
        self.assertEqual(self.invoke('config','ai','on').returncode,0)
        self.assertEqual(self.invoke('config','ai','off').returncode,0)
        result=self.invoke('config')
        self.assertIn('Resting HR reference (bpm): 50.0',result.stdout)
        self.assertIn('Weight (kg): 70.0',result.stdout)
        self.assertIn('Ironman 70.3',result.stdout)
        settings=json.loads((self.home/'settings.json').read_text())
        self.assertFalse(settings['ai_reports_enabled'])
        self.assertEqual(settings['weight_kg'],70.0)
        self.assertEqual(settings['resting_hr_bpm'],50.0)
        self.assertEqual(settings['race_goal'],'Ironman 70.3')

    def test_review_and_feedback_management(self):
        sample=PROJECT/'examples'/'sample_activities.json'
        result=self.invoke('--as-of','2026-11-08','manage','import',str(sample))
        self.assertEqual(result.returncode,0,result.stderr)
        result=self.invoke('--as-of','2026-11-08','review')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.home/'reports'/'2026-11-08_recovery.md').exists())
        note=self.invoke('--as-of','2026-11-08','manage','note','Tired legs after riding',
                         '--event-day','2026-11-07')
        self.assertEqual(note.returncode,0,note.stderr)
        records=self.invoke('--as-of','2026-11-08','manage','notes')
        self.assertIn('Tired legs after riding',records.stdout)
        self.assertIn('unlinked',records.stdout)
        linked=self.invoke('--as-of','2026-11-08','manage','link','1','102')
        self.assertEqual(linked.returncode,0,linked.stderr)
        self.assertIn('workout: 102',self.invoke('--as-of','2026-11-08','manage','notes').stdout)
        unlinked=self.invoke('--as-of','2026-11-08','manage','unlink','1')
        self.assertEqual(unlinked.returncode,0,unlinked.stderr)
        self.assertIn('unlinked',self.invoke('--as-of','2026-11-08','manage','notes').stdout)

    def test_workout_review_and_legacy_aliases(self):
        sample=PROJECT/'examples'/'sample_activities.json'
        self.invoke('--as-of','2026-11-08','import-json',str(sample))
        result=self.invoke('--as-of','2026-11-08','manage','workouts')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('RUNNING',result.stdout.upper())
        result=self.invoke('--as-of','2026-11-08','manage','uncertain')
        self.assertEqual(result.returncode,0,result.stderr)
        old=self.invoke('--as-of','2026-11-08','feedback-list')
        self.assertEqual(old.returncode,0,old.stderr)
        old=self.invoke('--as-of','2026-11-08','set','weight_kg','69')
        self.assertEqual(old.returncode,0,old.stderr)

    def test_question_and_backfill_route_to_existing_behavior(self):
        with patch.object(cli.legacy_cli,'main',return_value=0) as legacy:
            self.assertEqual(cli.main(['ask','Why were my legs heavy?']),0)
            legacy.assert_called_once_with(['ask','--question','Why were my legs heavy?'])
        with patch.object(cli.legacy_cli,'main',return_value=0) as legacy:
            self.assertEqual(cli.main(['ask','--question','Sore legs','--activity-id','123']),0)
            legacy.assert_called_once_with(['ask','--question','Sore legs','--activity-id','123'])
        with patch.object(cli.legacy_cli,'main',return_value=0) as legacy:
            self.assertEqual(cli.main(['manage','backfill','30','--refresh-notes']),0)
            legacy.assert_called_once_with(['sync','--backfill-days','30','--refresh-notes'])
        with patch.object(cli.legacy_cli,'main',return_value=0) as legacy:
            self.assertEqual(cli.main(['--as-of','2026-11-08','review']),0)
            legacy.assert_called_once_with(['--as-of','2026-11-08','report'])

    def test_bad_arguments_are_rejected(self):
        self.assertNotEqual(self.invoke('config','unknown','70').returncode,0)
        self.assertNotEqual(self.invoke('config','weight').returncode,0)
        self.assertNotEqual(self.invoke('manage','backfill','0').returncode,0)
        self.assertNotEqual(self.invoke('ask','hello','--question','world').returncode,0)

if __name__=='__main__':
    unittest.main()
