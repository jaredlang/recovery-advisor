"""Cli for the endurance recovery advisor."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

from .paths import REPORTS
from . import __version__
from .settings import load_config, save_config, now_local
from .storage import sql_open, state_get, state_set, status_put, iso_now, import_activity_file, activity_date
from .garmin_sync import sync_online, error_label
from .classification import (query_range, activity_classifications, set_classification_override)
from .analysis import context_data
from .reporting import write_report, ai_context_digest, safe_markdown
from .feedback import ask, record_feedback, checkin, infer_workout, require_workout

def main(argv:list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description='Personal Garmin training/recovery advisor')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--as-of',help='Local YYYY-MM-DD for offline testing / reporting')
    sub=parser.add_subparsers(dest='command',required=True)
    sync_parser=sub.add_parser('sync',help='Checkpoint-based Garmin sync and report refresh')
    sync_parser.add_argument('--backfill-days',type=int,
                             help='Manual re-scan for older late-uploaded data, e.g. 30')
    sync_parser.add_argument('--refresh-notes',action='store_true',
                             help='Also re-fetch stored activity details for the backfill window to capture edited descriptions')
    importer=sub.add_parser('import-json',help='Offline import of existing Garmin activities')
    importer.add_argument('file',type=Path)
    sub.add_parser('report',help='Regenerate report without Garmin network calls')
    asker=sub.add_parser('ask',help='Ask AI a question; question is saved as athlete feedback first')
    asker.add_argument('--question',help='Optional question text (otherwise prompted privately)')
    asker.add_argument('--event-day',help='YYYY-MM-DD when the experience happened; defaults to today')
    asker.add_argument('--activity-id',help='Associate the observation and answer with this Garmin workout')
    feedback_parser=sub.add_parser('feedback',help='Record an observation/question locally without AI')
    feedback_parser.add_argument('--question',help='Optional observation text (otherwise prompted)')
    feedback_parser.add_argument('--event-day',help='YYYY-MM-DD of experience; defaults to today')
    feedback_parser.add_argument('--activity-id',help='Associate observation with this Garmin workout')
    sub.add_parser('checkin',help='Add or replace subjective check-in')
    sub.add_parser('status',help='Show local data status')
    activity_parser=sub.add_parser('activities',help='List recent Garmin records with automatic classifications')
    activity_parser.add_argument('--days',type=int,default=30)
    review_parser=sub.add_parser('review-activities',help='Show only ambiguous Garmin records needing confirmation')
    review_parser.add_argument('--days',type=int,default=30)
    classify_parser=sub.add_parser('classify',help='Optional correction of an ambiguous automatic classification (stored in SQLite)')
    classify_parser.add_argument('activity_id',help='Existing Garmin activity ID')
    classify_parser.add_argument('label',choices=('workout','feedback_only','auto'))
    marked_parser=sub.add_parser('mark-feedback',help='Compatibility alias: confirm an activity as feedback-only in SQLite')
    marked_parser.add_argument('activity_id',help='Garmin activity ID (see activities command)')
    unmarked_parser=sub.add_parser('unmark-feedback',help='Compatibility alias: confirm an activity as a workout in SQLite')
    unmarked_parser.add_argument('activity_id',help='Garmin activity ID')
    feedback_list=sub.add_parser('feedback-list',help='List locally saved athlete feedback and workout links')
    feedback_list.add_argument('--days',type=int,default=30)
    link=sub.add_parser('link-feedback',help='Associate saved feedback with a local Garmin workout')
    link.add_argument('feedback_id',type=int)
    link.add_argument('activity_id')
    unlink=sub.add_parser('unlink-feedback',help='Remove a feedback/workout association')
    unlink.add_argument('feedback_id',type=int)
    setting=sub.add_parser('set',help='Set onboarding and AI options')
    setting.add_argument('key',choices=('race_goal','weight_kg','dietary_preferences','ai_reports_enabled',
                                        'openai_model','resting_hr_bpm','usual_bedtime','usual_wake_time',
                                        'baseline_days','analysis_days','feedback_reporting_mode'))
    setting.add_argument('value',help='New value; use true/false for ai_reports_enabled')
    args=parser.parse_args(argv)
    config=load_config()
    if args.as_of:
        today=dt.date.fromisoformat(args.as_of)
    else:
        today=now_local(config).date()
    if args.command=='set':
        value=args.value
        if args.key=='ai_reports_enabled':
            if value.lower() not in ('true','false'):
                parser.error('Use true or false')
            value=value.lower()=='true'
        elif args.key=='weight_kg':
            value=float(value)
            if not 20<=value<=300: parser.error('Body mass must be between 20 and 300 kg')
        elif args.key=='resting_hr_bpm':
            value=None if value.lower() in ('none','null','clear') else float(value)
            if value is not None and not 30<=value<=110:
                parser.error('Resting HR reference must be 30–110 bpm')
        elif args.key in ('usual_bedtime','usual_wake_time'):
            if value.lower() in ('none','null','clear'):
                value=None
            else:
                try: dt.time.fromisoformat(value)
                except ValueError: parser.error('Use HH:MM, e.g. 22:30')
                if len(value)!=5: parser.error('Use HH:MM, e.g. 22:30')
        elif args.key=='feedback_reporting_mode':
            if value not in ('issues_only','unspecified'):
                parser.error('Use issues_only or unspecified')
        elif args.key in ('baseline_days','analysis_days'):
            if not value.isdecimal() or not 1<=int(value)<=365:
                parser.error('Days must be 1–365')
            value=int(value)
            if args.key=='baseline_days' and value<int(config['analysis_days']):
                parser.error('Baseline must be >= analysis days')
            if args.key=='analysis_days' and value>int(config['baseline_days']):
                parser.error('Analysis days must be <= baseline days')
        config[args.key]=value
        save_config(config)
        print('Updated setting:',args.key,'(stored locally in settings.json)')
        return 0
    with contextlib.closing(sql_open()) as db:
        if args.command=='import-json':
            result=import_activity_file(db,args.file)
            print(json.dumps(result,indent=2))
            path=write_report(db,today,config)
            print('Report:',path)
        elif args.command=='sync':
            if args.backfill_days is not None and not 1<=args.backfill_days<=365:
                parser.error('--backfill-days must be 1–365')
            try:
                result=sync_online(db,today,config,backfill_days=args.backfill_days,refresh_notes=args.refresh_notes)
            except Exception as exc:
                label=error_label(exc)
                status_put(db,'authentication',False,label)
                db.execute('INSERT INTO runs(at,first_day,last_day,status,changed,message) VALUES(?,?,?,?,?,?)',
                           (iso_now(),None,str(today),'failed',0,label))
                db.commit()
                result={'status':'failed','changed':0,'errors':[label]}
            print('Garmin sync:',json.dumps(result,indent=2))
            # Always regenerate deterministic report for data freshness, including partial failures.
            path=write_report(db,today,config)
            print('Deterministic report:',path)
            ai_file=REPORTS/f'{today.isoformat()}_ai_review.md'
            digest=ai_context_digest(context_data(db,today,config))
            need_ai=(not ai_file.exists() or state_get(db,'last_ai_context_digest')!=digest)
            if config['ai_reports_enabled'] and result['status']!='failed' and need_ai:
                try:
                    ai_path=write_report(db,today,config,with_ai=True)
                    state_set(db,'last_ai_context_digest',digest)
                    db.commit()
                    print('AI report:',ai_path)
                except Exception as exc:
                    print('AI report unavailable:',type(exc).__name__,'(deterministic report preserved)',file=sys.stderr)
            return 1 if result['status']!='ok' else 0
        elif args.command=='report':
            path=write_report(db,today,config,with_ai=config['ai_reports_enabled'])
            print('Report:',path)
        elif args.command in ('ask','feedback'):
            question=args.question if args.question is not None else input('Observation or question: ').strip()
            try:
                event_day=dt.date.fromisoformat(args.event_day) if args.event_day else today
            except ValueError:
                parser.error('--event-day must use YYYY-MM-DD')
            if event_day>today:
                parser.error('Feedback event date cannot be in the future')
            activity_id=args.activity_id
            if activity_id is None:
                activity_id=infer_workout(db,event_day)
                if activity_id:
                    print('Associated automatically with the only confirmed workout on',event_day,':',activity_id)
                else:
                    print('Observation saved without activity link (none or multiple confirmed workouts that day).')
            if args.command=='ask':
                print('Answer:',ask(db,today,config,question,event_day,activity_id))
            else:
                fid=record_feedback(db,question,event_day,activity_id)
                print('Saved feedback ID:',fid)
                print('Updated report:',write_report(db,today,config))
        elif args.command=='checkin':
            checkin(db,today)
            print('Check-in saved; updated:',write_report(db,today,config))
        elif args.command in ('activities','review-activities'):
            if not 1<=args.days<=365: parser.error('--days must be 1–365')
            first=(today-dt.timedelta(days=args.days-1)).isoformat()
            records=query_range(db,dt.date.fromisoformat(first),today)
            for item,decision in reversed(activity_classifications(db,records)):
                if args.command=='review-activities' and decision['label']!='ambiguous':
                    continue
                title=str(item.get('activityName',''))[:85]
                print(f"{item.get('activityId')} | {activity_date(item)} | "
                      f"{item.get('activityType',{}).get('typeKey','unknown')} | "
                      f"{decision['label'].upper()} | {title} | {decision['reason']}")
                if args.command=='review-activities':
                    note=item.get('description') or item.get('notes')
                    if isinstance(note,str) and note.strip():
                        print('    Note preview:',safe_markdown(note)[:160])
            if args.command=='review-activities':
                print('If needed: recovery-advisor classify ACTIVITY_ID workout|feedback_only|auto')
        elif args.command in ('mark-feedback','unmark-feedback','classify'):
            if not args.activity_id.isdecimal() or not db.execute(
                    'SELECT 1 FROM activities WHERE id=?',(args.activity_id,)).fetchone():
                parser.error('Provide an existing Garmin activity ID; use `recovery-advisor activities`')
            if args.command=='mark-feedback': label='feedback_only'
            elif args.command=='unmark-feedback': label='workout'
            else: label=args.label
            set_classification_override(db,args.activity_id,label)
            db.commit()
            print('Classification:',args.activity_id,label,'(SQLite; settings.json unchanged)')
            print('Updated report:',write_report(db,today,config))
        elif args.command=='feedback-list':
            if not 1<=args.days<=365:
                parser.error('--days must be 1–365')
            first=(today-dt.timedelta(days=args.days-1)).isoformat()
            rows=db.execute("SELECT id,event_day,question,activity_id FROM feedback "
                "WHERE event_day BETWEEN ? AND ? ORDER BY id DESC",(first,today.isoformat()))
            for row in rows:
                print(f"{row['id']} | {row['event_day']} | workout: {row['activity_id'] or 'unlinked'} | {row['question'][:110]}")
        elif args.command in ('link-feedback','unlink-feedback'):
            row=db.execute('SELECT 1 FROM feedback WHERE id=?',(args.feedback_id,)).fetchone()
            if row is None:
                raise ValueError('Feedback ID not found; use `recovery-advisor feedback-list`')
            linked_id=None
            if args.command=='link-feedback':
                require_workout(db,args.activity_id)
                linked_id=args.activity_id
            db.execute('UPDATE feedback SET activity_id=? WHERE id=?',(linked_id,args.feedback_id))
            db.commit()
            print('Feedback',args.feedback_id,'linked workout:',linked_id or 'none')
        elif args.command=='status':
            count=db.execute('SELECT COUNT(*) FROM activities').fetchone()[0]
            dates=db.execute('SELECT MIN(day),MAX(day) FROM activities').fetchone()
            last=db.execute('SELECT at,status,message FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            print('Activities:',count,'Date range:',dates[0],'-',dates[1])
            print('Last sync:',dict(last) if last else 'none (offline imports are not live syncs)')
            for row in db.execute('SELECT kind,status,last_success,message FROM endpoint_status ORDER BY kind'):
                print('Endpoint',row['kind'],row['status'],'last success:',row['last_success'])
            for kind in ('activities','daily','sleep','hrv','body_battery'):
                print('Last successful',kind,'sync:',state_get(db,f'last_success_at_{kind}') or 'not yet completed')
            print('Feedback reports:',db.execute('SELECT COUNT(*) FROM feedback').fetchone()[0])
            print('Garmin wellness notes:',db.execute('SELECT COUNT(*) FROM garmin_notes').fetchone()[0])
            print('Activity classification overrides:',db.execute('SELECT COUNT(*) FROM activity_classification_overrides').fetchone()[0])
            print('AI report enabled:',config['ai_reports_enabled'])
    return 0


def run() -> None:
    """Console-script entry point with clear, privacy-preserving errors."""
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, ImportError, sqlite3.DatabaseError) as exc:
        print('Error:', str(exc), file=sys.stderr)
        sys.exit(1)
