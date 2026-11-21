import datetime as dt
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
        self.assertEqual(first['period'],['2026-10-26','2026-11-08'])
        self.assertEqual(first['new_activities'],2)
        self.assertEqual(len(f.details),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM wellness').fetchone()[0],4)
        second=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(second['period'],['2026-11-07','2026-11-08'])
        self.assertEqual(second['new_activities'],0)
        self.assertEqual(len(f.details),2)  # detail fetched only on change
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        f.activities[0]['duration']=3800
        third=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(third['updated_activities'],1)
        self.assertEqual(len(f.details),3)
    def test_optional_wellness_failure_does_not_repeat_full_backfill(self):
        class Broken(FakeGarmin):
            def get_daily_hrv_summaries(self,*args):raise RuntimeError('No HRV')
        f=Broken()
        result=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(a.state_get(self.db,'garmin_initial_sync_done'),'yes')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activities').fetchone()[0],2)
        later=a.sync_with_client(self.db,f,self.today,self.settings)
        self.assertEqual(later['period'],['2026-11-07','2026-11-08'])
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
        self.assertEqual(ctx['window']['baseline_start'],'2026-10-26')
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


if __name__=='__main__': unittest.main()

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
            with a.sql_open(Path(t)/'a.sqlite3') as db:
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
            with a.sql_open(Path(t)/'a.sqlite3') as db:
                f=DetailFlaky()
                first=a.sync_with_client(db,f,dt.date(2026,11,8),dict(a.DEFAULT_CONFIG))
                self.assertEqual(first['status'],'partial')
                missing=db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0]
                self.assertEqual(missing,1)
                second=a.sync_with_client(db,f,dt.date(2026,11,8),dict(a.DEFAULT_CONFIG))
                self.assertEqual(second['status'],'ok')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM activities WHERE detail IS NULL').fetchone()[0],0)
