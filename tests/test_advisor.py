import datetime as dt
import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import advisor as a


class FakeGarmin:
    def __init__(self):
        self.activities = [
            {'activityId':111, 'activityName':'Run', 'startTimeLocal':'2026-11-08 07:00:00',
             'duration':3600, 'distance':10000, 'activityType':{'typeKey':'running'},
             'ownerFullName':'SENSITIVE','startLatitude':29.9},
            {'activityId':112, 'activityName':'Bike', 'startTimeLocal':'2026-11-07 08:00:00',
             'duration':1800, 'distance':10000, 'activityType':{'typeKey':'virtual_ride'},
             'ownerFullName':'SENSITIVE','startLongitude':-95.2}
        ]
        self.details=[]
        self.searches=[]
        self.error_kind=None
    def search_activities(self,**kwargs):
        self.searches.append(kwargs)
        if self.error_kind=='activities': raise RuntimeError('test error')
        return self.activities
    def get_activity(self,activity_id):
        self.details.append(str(activity_id))
        return {'activityId':int(activity_id),'startTimeLocal':'2026-11-08 07:00:00',
                'duration':3600,'startLatitude':29.3,'ownerFullName':'SENSITIVE'}
    def get_daily_summaries(self,*args):
        return {'data':[{'calendarDate':'2026-11-08','restingHeartRate':51,'totalSteps':10000}]}
    def get_daily_sleep_summaries(self,*args):
        return {'data':[{'calendarDate':'2026-11-08','sleepTimeSeconds':27000,'sleepScore':80}]}
    def get_daily_hrv_summaries(self,*args):
        return [{'calendarDate':'2026-11-08','lastNightAvg':40}]
    def get_daily_body_battery(self,*args):
        return [{'calendarDate':'2026-11-08','bodyBatteryHigh':88}]


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=a.sql_open(Path(self.tmp.name)/'test.sqlite3')
        self.today=dt.date(2026,11,8)
        self.settings=dict(a.DEFAULT_CONFIG)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_idempotent_import_and_privacy(self):
        original={'activityId':44,'startTimeLocal':'2026-11-08 06:00:00',
                  'duration':2400,'distance':5000,'activityType':{'typeKey':'running'},
                  'ownerFullName':'REDACTME','startLatitude':29.9,'ownerId':900}
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'activities.json'
            path.write_text(json.dumps([original]))
            first=a.import_activity_file(self.db,path)
            second=a.import_activity_file(self.db,path)
        self.assertEqual(first['added'],1)
        self.assertEqual(second['added'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],1)
        serialized=self.db.execute('SELECT payload FROM activities').fetchone()[0]
        self.assertNotIn('REDACTME',serialized)
        self.assertNotIn('Latitude',serialized)
        self.assertNotIn('ownerId',serialized)
    def test_changed_activity_updates_not_duplicates(self):
        base={'activityId':10,'startTimeLocal':'2026-11-08 06:00:00','duration':1200}
        a.insert_activity(self.db,base)
        self.assertFalse(a.insert_activity(self.db,base)[0])
        changed={**base,'duration':1300}
        self.assertEqual(a.insert_activity(self.db,changed),(True,False))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],1)
    def test_initial_and_incremental_sync_and_recovery(self):
        f=FakeGarmin()
        first=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(first['status'],'ok')
        self.assertEqual(first['period'],['2026-10-10','2026-11-08'])
        self.assertEqual(first['new_activities'],2)
        self.assertEqual(len(f.details),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wellness').fetchone()[0],4)
        second=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(second['period'],['2026-11-08','2026-11-08'])
        self.assertEqual(second['new_activities'],0)
        self.assertEqual(len(f.details),4)  # today/yesterday details refreshed to capture edited notes
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        f.activities[0]['duration']=3800
        third=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(third['updated_activities'],1)
        self.assertEqual(len(f.details),6)
    def test_optional_wellness_failure_does_not_repeat_full_backfill(self):
        class Broken(FakeGarmin):
            def get_daily_hrv_summaries(self,*args):raise RuntimeError('No HRV')
        f=Broken()
        result=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(a.state_get(self.db,'garmin_initial_sync_done'),'yes')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        later=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(later['period'],['2026-11-08','2026-11-08'])
    def test_daily_unrecognized_format_not_mapped_to_wrong_date(self):
        data={'data':[{'someValue':21},{'calendarDate':'2026-11-09','restingHeartRate':40}]}
        rows=a.unpack_days(data,'daily',dt.date(2026,11,8),self.today)
        self.assertEqual(rows,{})
    def test_context_restricts_identifiers_and_7days(self):
        self.db.execute("INSERT INTO checkins VALUES(?,?,?,?,?,?,?)",('2026-11-08',3,2,'normal','Tired legs','rice',a.iso_now()))
        a.insert_activity(self.db,{'activityId':55,'startTimeLocal':'2026-11-08 08:00:00','activityName':'Private City','duration':900,'distance':3500,'activityType':{'typeKey':'running'}})
        ctx=a.context_data(self.db,self.today,self.settings)
        serialized=json.dumps(ctx)
        self.assertNotIn('Private City',serialized)
        self.assertNotIn('"activityId"',serialized)
        self.assertEqual(ctx['window']['analysis_start'],'2026-11-02')
        self.assertEqual(ctx['window']['baseline_start'],'2026-10-10')
        self.assertEqual(ctx['current']['activities'],1)
        self.assertIn('Tired legs',serialized)
        doc=a.deterministic_report(ctx)
        self.assertIn('Tired legs',doc)
    def test_failed_activity_search_not_marked_success(self):
        f=FakeGarmin();f.error_kind='activities'
        result=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],0)
        self.assertIsNone(a.state_get(self.db,'garmin_initial_sync_done'))




class RateLimitTests(unittest.TestCase):
    def test_429_stops_further_wellness_calls_and_preserves_activities(self):
        class TooManyRequests(Exception):
            status_code=429
        class RateLimited(FakeGarmin):
            def __init__(self):
                super().__init__()
                self.wellness_calls=[]
            def get_daily_summaries(self,*args):
                self.wellness_calls.append('daily')
                raise TooManyRequests()
            def get_daily_sleep_summaries(self,*args):
                self.wellness_calls.append('sleep')
                return []
        with tempfile.TemporaryDirectory() as t:
            with contextlib.closing(a.sql_open(Path(t)/'a.sqlite3')) as db:
                f=RateLimited()
                result=a.sync_with_client(db,f,dt.date(2026,11,8),dict(a.DEFAULT_CONFIG))
                self.assertEqual(result['status'],'partial')
                self.assertEqual(f.wellness_calls,['daily'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)

    def test_missing_details_retried_on_next_incremental_run(self):
        class DetailFlaky(FakeGarmin):
            def __init__(self):
                super().__init__()
                self.fail_once=True
            def get_activity(self,activity_id):
                if self.fail_once:
                    self.fail_once=False
                    raise RuntimeError('transient test failure')
                return super().get_activity(activity_id)
        with tempfile.TemporaryDirectory() as t:
            with contextlib.closing(a.sql_open(Path(t)/'a.sqlite3')) as db:
                f=DetailFlaky()
                first=a.sync_with_client(db,f,dt.date(2026,11,8),dict(a.DEFAULT_CONFIG))
                self.assertEqual(first['status'],'partial')
                missing=db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0]
                self.assertEqual(missing,1)
                second=a.sync_with_client(db,f,dt.date(2026,11,8),dict(a.DEFAULT_CONFIG))
                self.assertEqual(second['status'],'ok')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0],0)


class RevisedProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=a.sql_open(Path(self.tmp.name)/'test.sqlite3')
        self.today=dt.date(2026,11,8)
        self.settings=dict(a.DEFAULT_CONFIG)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_default_30_day_baseline_and_assumptions_from_user_settings(self):
        self.assertEqual(self.settings['baseline_days'],30)
        self.assertNotIn('daily_sync_lookback_days',self.settings)
        self.settings.update(resting_hr_bpm=52,usual_bedtime='22:30',usual_wake_time='06:30')
        ctx=a.context_data(self.db,self.today,self.settings)
        ref=ctx['baseline_references']
        self.assertEqual(ref['resting_hr_reference']['source'],'user_setting')
        self.assertEqual(ref['resting_hr_reference']['bpm'],52)
        self.assertEqual(ref['usual_sleep_schedule']['planned_sleep_opportunity_hours'],8.0)
        self.assertIsNone(ref['garmin_sleep_median_hours'])
        report=a.deterministic_report(ctx)
        self.assertIn('Resting HR reference: 52 bpm',report)
        self.assertIn('NOT observed sleep duration',report)
        self.assertIn('Assumption / evidence limit',report)
        self.assertIn('2026-10-10',report)

    def test_medial_rhr_requires_multiple_measured_days(self):
        for i,val in enumerate([55,53,52,54,51]):
            d=self.today-dt.timedelta(days=i)
            a.insert_wellness(self.db,'daily',{'data':[{'calendarDate':str(d),'restingHeartRate':val}]},d,d)
        ref=a.context_data(self.db,self.today,self.settings)['baseline_references']
        self.assertEqual(ref['resting_hr_reference']['bpm'],53)
        self.assertEqual(ref['resting_hr_reference']['source'],'garmin_30_day_median')
        self.assertEqual(ref['recent_resting_hr_days'],5)

    def test_resume_after_four_day_outage_fetches_all_gap_days(self):
        fake=FakeGarmin()
        first=a.sync_with_client(self.db,fake,self.today,self.settings)
        self.assertEqual(first['status'],'ok')
        after=self.today+dt.timedelta(days=4)
        fake.activities.append({'activityId':113,'startTimeLocal':'2026-11-10 06:00:00',
                                'duration':1200,'distance':4000,'activityType':{'typeKey':'running'}})
        second=a.sync_with_client(self.db,fake,after,self.settings)
        self.assertEqual(second['period'],['2026-11-08','2026-11-12'])
        self.assertEqual(fake.searches[-1]['start_date'],'2026-11-08')
        self.assertEqual(second['new_activities'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],3)
        self.assertEqual(a.state_get(self.db,'last_success_day_activities'),'2026-11-12')

    def test_failed_activities_keeps_checkpoint_and_catches_up(self):
        fake=FakeGarmin()
        a.sync_with_client(self.db,fake,self.today,self.settings)
        fake.error_kind='activities'
        later=self.today+dt.timedelta(days=3)
        failure=a.sync_with_client(self.db,fake,later,self.settings)
        self.assertEqual(failure['status'],'failed')
        self.assertEqual(a.state_get(self.db,'last_success_day_activities'),'2026-11-08')
        fake.error_kind=None
        a.sync_with_client(self.db,fake,later+dt.timedelta(days=2),self.settings)
        self.assertEqual(fake.searches[-1]['start_date'],'2026-11-08')

    def test_recovery_endpoint_independent_catchup_after_failure(self):
        class Flaky(FakeGarmin):
            def __init__(self):
                super().__init__()
                self.fail_hrv=True
                self.hrv_calls=[]
                self.sleep_calls=[]
            def get_daily_hrv_summaries(self,*args):
                self.hrv_calls.append(args)
                if self.fail_hrv: raise RuntimeError('HRV temporarily unavailable')
                return [{'calendarDate':'2026-11-09','lastNightAvg':40}]
            def get_daily_sleep_summaries(self,*args):
                self.sleep_calls.append(args)
                return []
        f=Flaky()
        result=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(a.state_get(self.db,'last_success_day_hrv'))
        self.assertEqual(a.state_get(self.db,'last_success_day_sleep'),'2026-11-08')
        f.fail_hrv=False
        a.sync_with_client(self.db,f,self.today+dt.timedelta(days=4),self.settings)
        self.assertEqual(f.hrv_calls[-1],('2026-10-14','2026-11-12'))
        self.assertEqual(f.sleep_calls[-1],('2026-11-08','2026-11-12'))
        self.assertEqual(a.state_get(self.db,'last_success_day_hrv'),'2026-11-12')

    def test_existing_install_migrates_without_discarding_settings(self):
        from unittest.mock import patch
        config_file=Path(self.tmp.name)/'settings.json'
        config_file.write_text(json.dumps({'baseline_days':14,'daily_sync_lookback_days':2,
                                           'race_goal':'Triathlon'}))
        with patch.object(a,'CONFIG_PATH',config_file):
            config=a.load_config()
        self.assertEqual(config['race_goal'],'Triathlon')
        self.assertEqual(config['baseline_days'],30)  # Upgrade the old shipped default
        self.assertIn('resting_hr_bpm',config)
        self.assertNotIn('daily_sync_lookback_days',config_file.read_text())
        # Upgrader handles old DB lacking new checkpoints by requesting the full configured period.
        self.assertEqual(a.endpoint_start(self.db,'activities',self.today,{**config,'baseline_days':30}),dt.date(2026,10,10))

    def test_questions_are_feedback_without_optional_checkins(self):
        fid=a.record_feedback(self.db,'Why did I feel unusually heavy legs after yesterday?',self.today)
        self.assertEqual(fid,1)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(len(ctx['athlete_feedback']),1)
        self.assertEqual(ctx['self_reports'],[])
        self.assertIn('heavy legs',a.deterministic_report(ctx))
        self.assertNotIn('ownerFullName',json.dumps(ctx))

    def test_ask_persists_feedback_even_when_ai_unavailable(self):
        from unittest.mock import patch
        with patch.object(a,'REPORTS',Path(self.tmp.name)/'reports'):
            with self.assertRaisesRegex(RuntimeError,'OPENAI_API_KEY'):
                with patch.dict('os.environ',{'OPENAI_API_KEY':''}):
                    a.ask(self.db,self.today,self.settings,question='Sore legs after long run')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM feedback').fetchone()[0],1)

    def test_missing_old_details_retried_after_long_gap(self):
        class AlwaysMissOne(FakeGarmin):
            def get_activity(self,activity_id):
                if str(activity_id)=='111':raise RuntimeError('temporary')
                return super().get_activity(activity_id)
        f=AlwaysMissOne()
        a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0],1)
        f.get_activity=lambda activity_id:{'activityId':int(activity_id),'duration':3600}
        a.sync_with_client(self.db,f,self.today+dt.timedelta(days=15),self.settings)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0],0)


class NotesAndRecoveryV21Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=a.sql_open(Path(self.tmp.name)/'test.sqlite3')
        self.today=dt.date(2026,11,8)
        self.settings=dict(a.DEFAULT_CONFIG)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_garmin_description_and_details_are_preserved_but_no_private_fields(self):
        a.insert_activity(self.db,{'activityId':9,'startTimeLocal':'2026-11-08 08:00:00',
                       'activityType':{'typeKey':'other'},'description':'Legs felt heavy',
                       'startLongitude':-95,'ownerFullName':'PRIVATE'})
        a.upsert_detail(self.db,'9',{'activityId':9,'description':'Legs felt heavy after my workout',
                                      'summaryDTO':{'startLatitude':30},'ownerFullName':'PRIVATE'})
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['garmin_observations'][0]['text'],'Legs felt heavy after my workout')
        self.assertEqual(ctx['current']['activities'],0)  # Text-only Other entry is automatically feedback.
        self.assertEqual(ctx['feedback_only_activity_count'],1)
        self.assertNotIn('PRIVATE',json.dumps(ctx))
        self.assertNotIn('startLongitude',json.dumps(ctx))
        self.assertIn('heavy',a.deterministic_report(ctx))

    def test_wellness_notes_import_and_dedupe(self):
        note={'calendarDate':'2026-11-08','activityUuid':'abc','activityName':'My recovery',
              'notes':'Head felt heavy after training', 'userProfilePk':999, 'startLatitude':31}
        self.assertEqual(a.insert_garmin_notes(self.db,[note],self.today,self.today),1)
        self.assertEqual(a.insert_garmin_notes(self.db,[note],self.today,self.today),0)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],0)
        self.assertEqual(len(ctx['garmin_observations']),1)
        self.assertEqual(ctx['garmin_observations'][0]['source'],'wellness_activity_note')
        self.assertNotIn('userProfilePk',json.dumps(ctx))

    def test_absence_of_feedback_weakly_positive_only_and_low_confidence(self):
        empty=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(empty['recovery_score']['score'],72)
        self.assertEqual(empty['recovery_score']['confidence'],'low')
        self.assertFalse(empty['recovery_score']['validated'])
        a.record_feedback(self.db,'Why is my heart rate higher?',self.today)
        context=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(context['recovery_score']['score'],70)

    def test_authentication_details_kept_out_of_athlete_report(self):
        a.status_put(self.db,'activities',False,'HTTP 401 authorization')
        self.db.commit()
        context=a.context_data(self.db,self.today,self.settings)
        report=a.deterministic_report(context)
        self.assertIn('may be missing',report)
        self.assertNotIn('HTTP 401',report)
        self.assertNotIn('authentication',report.lower())
        self.assertNotIn('HTTP 401',str({k:v for k,v in context.items() if k not in ('endpoint_statuses','sync_status')}))

    def test_feedback_only_activity_excluded_from_training_but_title_used_as_observation(self):
        a.insert_activity(self.db,{'activityId':901,'startTimeLocal':'2026-11-08 07:00:00',
                         'activityName':'Leg soreness after run','duration':3000,'distance':9500,
                         'activityType':{'typeKey':'other'}})
        # Automatically classified using its symptom-style title, no ID setting.
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],0)
        self.assertEqual(ctx['garmin_observations'][0]['text'],'Leg soreness after run')
        self.assertEqual(ctx['garmin_observations'][0]['source'],'garmin_feedback_only_activity')
        self.assertEqual(ctx['recovery_score']['score'],70)

    def test_garmin_self_evaluation_rpe_feel_preserved_and_influences_provisional_score(self):
        a.insert_activity(self.db,{'activityId':42,'startTimeLocal':'2026-11-08 07:00:00',
             'duration':2400,'activityType':{'typeKey':'running'}})
        a.upsert_detail(self.db,'42',{'activityId':42,'summaryDTO':{
            'directWorkoutRpe':70,'directWorkoutFeel':25,'startLatitude':30}})
        ctx=a.context_data(self.db,self.today,self.settings)
        workout=ctx['recent_workouts'][0]
        self.assertEqual(workout['self_evaluation_rpe_0_to_10'],7.0)
        self.assertEqual(workout['self_evaluation_feel_0_to_100'],25.0)
        self.assertEqual(ctx['recovery_score']['score'],68) # 70 - 4 weak feel + 2 silence
        self.assertNotIn('startLatitude',json.dumps(ctx))

    def test_notes_failure_prevents_positive_silence_inference(self):
        a.status_put(self.db,'garmin_notes',False,'HTTP 401 invalid token')
        self.db.commit()
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['recovery_score']['score'],70)
        self.assertIn('could not be checked',ctx['retrieval_impact'])
        self.assertNotIn('HTTP 401',a.deterministic_report(ctx))

    def test_new_notes_can_be_refetched_via_recent_activity_detail(self):
        class NotesGarmin(FakeGarmin):
            def __init__(self):
                super().__init__(); self.description='First draft'
            def get_activity(self,activity_id):
                data=super().get_activity(activity_id)
                data['description']=self.description
                return data
        g=NotesGarmin()
        a.sync_with_client(self.db,g,self.today,self.settings)
        g.description='Heavy legs in evening'
        a.sync_with_client(self.db,g,self.today,self.settings)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertIn('Heavy legs in evening',json.dumps(ctx['garmin_observations']))


class LateUploadTests(unittest.TestCase):
    def test_manual_backfill_scans_older_uploads(self):
        with tempfile.TemporaryDirectory() as t:
            with contextlib.closing(a.sql_open(Path(t)/'db.sqlite3')) as db:
                fake=FakeGarmin()
                today=dt.date(2026,11,8)
                a.sync_with_client(db,fake,today,dict(a.DEFAULT_CONFIG))
                later=today+dt.timedelta(days=10)
                # Upload of activity performed before the last successful sync.
                fake.activities.append({'activityId':333,'startTimeLocal':'2026-11-05 08:00:00',
                                        'duration':2400,'activityType':{'typeKey':'running'}})
                a.sync_with_client(db,fake,later,dict(a.DEFAULT_CONFIG))
                self.assertIsNone(db.execute('SELECT id FROM activities WHERE id=?',('333',)).fetchone())
                backfill=a.sync_with_client(db,fake,later,dict(a.DEFAULT_CONFIG),backfill_days=30)
                self.assertEqual(backfill['new_activities'],1)
                self.assertEqual(backfill['period'],['2026-10-20','2026-11-18'])


class AutomaticClassificationV22Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.sqlite3'
        self.db=a.sql_open(self.path)
        self.today=dt.date(2026,11,8)
        self.settings=dict(a.DEFAULT_CONFIG)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _insert(self, activity_id, name, kind='other', duration=0, distance=0, **kwargs):
        item={'activityId':activity_id,'activityName':name,
              'startTimeLocal':'2026-11-08 08:00:00','duration':duration,
              'distance':distance,'activityType':{'typeKey':kind},**kwargs}
        a.insert_activity(self.db,item)
        return item

    def test_no_manual_id_setting_in_defaults_or_example(self):
        self.assertNotIn('notes_only_activity_ids',a.DEFAULT_CONFIG)
        sample=json.loads((Path(a.__file__).parent/'settings.example.json').read_text())
        self.assertNotIn('notes_only_activity_ids',sample)

    def test_real_workout_with_symptom_note_is_never_discarded(self):
        self._insert(201,'Morning Run','running',2700,8000,
                     description='Legs felt heavy after the hills',averageHR=135)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],1)
        self.assertEqual(ctx['current']['duration_minutes'],45.0)
        self.assertIn('heavy',json.dumps(ctx['garmin_observations']))
        self.assertEqual(ctx['feedback_only_activity_count'],0)

    def test_other_note_with_false_metrics_not_counted(self):
        # Garmin can assign a duration and distance to an entry authored as feedback.
        self._insert(202,'Leg soreness after run','other',3000,9500,
                     isManualActivity=True)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],0)
        self.assertEqual(ctx['feedback_only_activity_count'],1)
        self.assertEqual(ctx['garmin_observations'][0]['text'],'Leg soreness after run')
        self.assertEqual(ctx['recovery_score']['score'],70)
        self.assertTrue(a.clean_activity({'isManualActivity':True}).get('isManualActivity'))

    def test_explicit_notes_title_is_feedback_even_with_duration(self):
        self._insert(210,'Notes','other',3300,8500,
                     description='I felt exhausted following yesterday\'s tempo run')
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['feedback_only_activity_count'],1)
        self.assertEqual(ctx['current']['activities'],0)
        self.assertIn('exhausted',json.dumps(ctx['garmin_observations']))

    def test_other_generic_activity_is_ambiguous_not_silently_discarded(self):
        self._insert(203,'Other','other',3600,10000)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],1)
        self.assertEqual(len(ctx['classification_review']),1)
        self.assertEqual(ctx['recovery_score']['score'],70)
        self.assertIn('provisionally counted',a.deterministic_report(ctx))
        self.assertNotIn('"activityId"',json.dumps(ctx))

    def test_real_other_workout_remains_workout(self):
        self._insert(204,'Strength and mobility','other',1800,0)
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['current']['activities'],1)
        self.assertEqual(ctx['classification_review'],[])

    def test_activity_details_reclassify_without_an_id_setting(self):
        self._insert(205,'Other','other',30,0)
        self.assertEqual(a.context_data(self.db,self.today,self.settings)['classification_review'][0]['day'],
                         '2026-11-08')
        a.upsert_detail(self.db,'205',{'activityId':205,'description':'Sore legs after cycling'})
        ctx=a.context_data(self.db,self.today,self.settings)
        self.assertEqual(ctx['feedback_only_activity_count'],1)
        self.assertEqual(ctx['current']['activities'],0)
        self.assertEqual(ctx['classification_review'],[])

    def test_optional_override_persists_in_sqlite_and_can_be_reset(self):
        self._insert(206,'Other','other',2000,8000)
        self.assertEqual(len(a.context_data(self.db,self.today,self.settings)['classification_review']),1)
        a.set_classification_override(self.db,'206','feedback_only')
        self.db.commit()
        with contextlib.closing(a.sql_open(self.path)) as other:
            self.assertEqual(a.context_data(other,self.today,self.settings)['feedback_only_activity_count'],1)
            a.set_classification_override(other,'206','auto')
            other.commit()
        self.assertEqual(len(a.context_data(self.db,self.today,self.settings)['classification_review']),1)
        self.assertNotIn('notes_only_activity_ids',self.settings)

    def test_migration_moves_legacy_ids_into_sqlite_and_removes_from_settings(self):
        from unittest.mock import patch
        config_file=Path(self.tmp.name)/'settings.json'
        config_file.write_text(json.dumps({
            'notes_only_activity_ids':['207','bad-id'],
            'resting_hr_bpm':50,'weight_kg':70,'race_goal':'My updated goal',
            'baseline_days':30}))
        self._insert(207,'Other','other',1200,5000)
        self.db.commit()  # Migration opens its own connection.
        with patch.object(a,'CONFIG_PATH',config_file),patch.object(a,'DB',self.path):
            config=a.load_config()
            config2=a.load_config()
        self.assertNotIn('notes_only_activity_ids',config)
        self.assertNotIn('notes_only_activity_ids',config_file.read_text())
        self.assertEqual(config2['weight_kg'],70)
        self.assertEqual(config2['race_goal'],'My updated goal')
        self.assertEqual(a.classification_overrides(self.db)['207'],'feedback_only')
        self.assertEqual(a.context_data(self.db,self.today,config)['current']['activities'],0)

    def test_actual_uploaded_sample_runs_and_rides_classify_as_workouts(self):
        path=Path(a.__file__).parents[1]/'garmin_activities.json'
        # Original five-workout file may not be present in an installed package.
        if not path.exists(): return
        sample=json.loads(path.read_text())
        self.assertTrue(all(a.classify_activity(a.clean_activity(x))['label']=='workout' for x in sample))
