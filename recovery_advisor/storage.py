"""Storage for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .paths import DB
from .constants import ACTIVITY_KEYS, METRIC_FIELDS

def strip_legacy_advisor_section(description: str) -> str:
    """Ignore old AI-authored sections when importing human Garmin workout notes.

    Historical versions may have inserted a marker-delimited block. This read-
    only sanitizer prevents previously generated AI text from being interpreted
    as new athlete feedback. It never modifies anything in Garmin.
    """
    begin='[Recovery Advisor:BEGIN]'
    end='[Recovery Advisor:END]'
    if begin not in description:
        return description
    before, remainder=description.split(begin,1)
    if end not in remainder:
        # Conservative: discard the apparent machine-authored trailing section.
        return before.rstrip()
    return (before + remainder.split(end,1)[1]).strip()


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def sql_open(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS activities (
      id TEXT PRIMARY KEY, day TEXT NOT NULL, start_local TEXT, kind TEXT,
      payload TEXT NOT NULL, detail TEXT, digest TEXT NOT NULL, detail_digest TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_activities_day ON activities(day);
    CREATE TABLE IF NOT EXISTS wellness (
      kind TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
      digest TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY(kind, day)
    );
    CREATE TABLE IF NOT EXISTS checkins (
      day TEXT PRIMARY KEY, fatigue INTEGER, soreness INTEGER, energy TEXT,
      symptoms TEXT, food_notes TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      received_at TEXT NOT NULL,
      event_day TEXT NOT NULL,
      question TEXT NOT NULL,
      answer TEXT,
      activity_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_feedback_day ON feedback(event_day);
    CREATE TABLE IF NOT EXISTS activity_classification_overrides (
      activity_id TEXT PRIMARY KEY, label TEXT NOT NULL
        CHECK(label IN ('workout','feedback_only')),
      origin TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS garmin_notes (
      id TEXT PRIMARY KEY, day TEXT NOT NULL, label TEXT, note TEXT NOT NULL,
      source TEXT NOT NULL, digest TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_garmin_notes_day ON garmin_notes(day);
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY, at TEXT NOT NULL, first_day TEXT, last_day TEXT,
      status TEXT NOT NULL, changed INTEGER NOT NULL, message TEXT
    );
    CREATE TABLE IF NOT EXISTS endpoint_status (
      kind TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
      status TEXT, message TEXT
    );
    CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
    ''')
    # Existing v2.3 installations have no feedback.activity_id column.
    columns={row['name'] for row in db.execute('PRAGMA table_info(feedback)')}
    if 'activity_id' not in columns:
        db.execute('ALTER TABLE feedback ADD COLUMN activity_id TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS idx_feedback_activity ON feedback(activity_id)')
    db.commit()
    return db

def to_float(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def activity_date(record: dict) -> str | None:
    value = record.get('startTimeLocal') or record.get('startTimeGMT')
    date = str(value or '')[:10]
    try:
        return dt.date.fromisoformat(date).isoformat()
    except ValueError:
        return None

def clean_activity(record: dict) -> dict:
    clean = {k: record[k] for k in ACTIVITY_KEYS if k in record and k not in ('description','notes')}
    for key in ('description','notes'):
        if isinstance(record.get(key),str) and record[key].strip():
            clean[key]=strip_legacy_advisor_section(record[key]).strip()[:2000]
    kind = record.get('activityType')
    if isinstance(kind, dict):
        clean['activityType'] = {'typeKey': str(kind.get('typeKey', 'unknown'))[:64]}
    return clean

def clean_detail(record: Any) -> dict:
    if not isinstance(record, dict):
        return {}
    # Detail payload is NOT stored raw; it can contain GPS tracks and identifiers.
    clean = clean_activity(record)
    if 'activityId' not in clean and isinstance(record.get('activityId'), int):
        clean['activityId'] = record['activityId']
    # Garmin activity detail has top-level description and nested summaryDTO; never
    # serialize summaryDTO/metadataDTO wholesale (location and account identifiers).
    for parent in ('summaryDTO',):
        nested=record.get(parent)
        if isinstance(nested,dict):
            for field in ('directWorkoutRpe','directWorkoutFeel'):
                value=to_float(nested.get(field))
                if value is not None and 0<=value<=100:
                    clean[field]=value
            for field in ('description','notes'):
                if field not in clean and isinstance(nested.get(field),str) and nested[field].strip():
                    clean[field]=strip_managed(nested[field]).strip()[:2000]
    return clean

def insert_activity(db: sqlite3.Connection, raw: dict) -> tuple[bool, bool]:
    activity = clean_activity(raw)
    day = activity_date(activity)
    key = str(activity.get('activityId', ''))
    if not day or not key or not key.isdecimal():
        return False, False
    signature = sha(activity)
    old = db.execute('SELECT digest,detail,detail_digest FROM activities WHERE id=?',(key,)).fetchone()
    if old and old['digest'] == signature:
        return False, False
    db.execute('''INSERT INTO activities(id,day,start_local,kind,payload,detail,digest,detail_digest,updated_at)
       VALUES(?,?,?,?,?,?,?,?,?)
       ON CONFLICT(id) DO UPDATE SET day=excluded.day, start_local=excluded.start_local,
         kind=excluded.kind,payload=excluded.payload,digest=excluded.digest,updated_at=excluded.updated_at,
         detail=CASE WHEN activities.digest<>excluded.digest THEN NULL ELSE activities.detail END,
         detail_digest=CASE WHEN activities.digest<>excluded.digest THEN NULL ELSE activities.detail_digest END''',
        (key,day,activity.get('startTimeLocal'),activity.get('activityType',{}).get('typeKey','unknown'),
         json.dumps(activity),None,signature,None,iso_now()))
    return True, old is None

def upsert_detail(db: sqlite3.Connection, activity_id: str, raw: Any) -> bool:
    item = clean_detail(raw)
    if not item:
        return False
    signature = sha(item)
    existing = db.execute('SELECT detail_digest FROM activities WHERE id=?',(activity_id,)).fetchone()
    if existing is None or existing['detail_digest'] == signature:
        return False
    db.execute('UPDATE activities SET detail=?, detail_digest=?,updated_at=? WHERE id=?',
               (json.dumps(item),signature,iso_now(),activity_id))
    return True

def nested_first_date(item: dict) -> str | None:
    for key in ('calendarDate','date','summaryDate','sleepDate','startDate'):
        value = item.get(key)
        if value is None:
            continue
        try:
            return dt.date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            continue
    return None

def unpack_days(data: Any, kind: str, start: dt.date, end: dt.date) -> dict[str, dict]:
    """Return dated records only; never guess dates for anonymous aggregate arrays."""
    if isinstance(data, dict):
        if isinstance(data.get('data'), list):
            rows = data['data']
        elif isinstance(data.get('dailySummaries'), list):
            rows = data['dailySummaries']
        elif isinstance(data.get('dailySleepSummaries'), list):
            rows = data['dailySleepSummaries']
        elif isinstance(data.get('hrvSummaries'), list):
            rows = data['hrvSummaries']
        elif nested_first_date(data):
            rows = [data]
        else:
            rows = []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    grouped: dict[str,dict] = {}
    for record in rows:
        if not isinstance(record, dict):
            continue
        day = nested_first_date(record)
        if not day or not (start.isoformat() <= day <= end.isoformat()):
            continue
        metrics = {k:record[k] for k in METRIC_FIELDS[kind] if k in record and record[k] is not None
                   and isinstance(record[k],(str,int,float,bool))}
        # Nested Garmin sleep summaries / HRV status variants: explicitly map known scalar keys only.
        if kind == 'sleep':
            score = record.get('sleepScores')
            if isinstance(score,dict):
                overall = score.get('overall')
                if isinstance(overall,dict):
                    v = overall.get('value')
                    if isinstance(v,(int,float)):
                        metrics['sleepScore'] = v
        if kind == 'hrv':
            summary = record.get('hrvSummary')
            if isinstance(summary,dict):
                for key in ('weeklyAvg','lastNightAvg','status'):
                    if isinstance(summary.get(key),(int,float,str)):
                        metrics[key] = summary[key]
        if not metrics:
            continue  # A dated shell without measurements is not valid recovery data.
        metrics['calendarDate'] = day
        grouped[day] = metrics
    return grouped

def insert_wellness(db:sqlite3.Connection,kind:str, payload:Any,start:dt.date,end:dt.date) -> int:
    updated = 0
    for day,record in unpack_days(payload,kind,start,end).items():
        digest=sha(record)
        old=db.execute('SELECT digest FROM wellness WHERE kind=? AND day=?',(kind,day)).fetchone()
        if old and old['digest']==digest:
            continue
        db.execute('''INSERT INTO wellness(kind,day,payload,digest,updated_at) VALUES(?,?,?,?,?)
                      ON CONFLICT(kind,day) DO UPDATE SET payload=excluded.payload,
                      digest=excluded.digest,updated_at=excluded.updated_at''',
                   (kind,day,json.dumps(record),digest,iso_now()))
        updated+=1
    return updated

def insert_garmin_notes(db:sqlite3.Connection,payload:Any,start:dt.date,end:dt.date) -> int:
    """Import explicitly written wellness-activity notes without inferring symptoms.

    Uses a stable record identity, and stores neither GPS nor user/profile metadata.
    """
    if isinstance(payload,list): rows=payload
    elif isinstance(payload,dict): rows=next((payload[k] for k in ('data','wellnessActivitySummaries','activities')
                                           if isinstance(payload.get(k),list)),[])
    else: rows=[]
    changed=0
    for item in rows:
        if not isinstance(item,dict): continue
        day=nested_first_date(item)
        note=item.get('notes')
        if not day or not start.isoformat()<=day<=end.isoformat() or not isinstance(note,str) or not note.strip():
            continue
        note=note.strip()[:2000]
        label=str(item.get('activityName') or 'Wellness activity')[:120]
        stable=str(item.get('activityUuid') or item.get('activityId') or item.get('startTimestampLocal') or item.get('startTimestampGMT') or '')
        if not stable: stable=sha({'day':day,'label':label,'note':note})
        ident=sha({'source':'garmin_wellness','id':stable})
        digest=sha({'day':day,'label':label,'note':note})
        old=db.execute('SELECT digest FROM garmin_notes WHERE id=?',(ident,)).fetchone()
        if old and old['digest']==digest: continue
        db.execute("""INSERT INTO garmin_notes(id,day,label,note,source,digest,updated_at) VALUES(?,?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET day=excluded.day,label=excluded.label,
                      note=excluded.note,digest=excluded.digest,updated_at=excluded.updated_at""",
                   (ident,day,label,note,'garmin_wellness',digest,iso_now()))
        changed+=1
    return changed

def state_get(db:sqlite3.Connection,key:str) -> str | None:
    row=db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
    return row['value'] if row else None

def state_set(db:sqlite3.Connection,key:str,value:str) -> None:
    db.execute('INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

def status_put(db:sqlite3.Connection,kind:str,ok:bool,message:str='') -> None:
    previous=db.execute('SELECT last_success FROM endpoint_status WHERE kind=?',(kind,)).fetchone()
    last_success=iso_now() if ok else (previous['last_success'] if previous else None)
    db.execute('''INSERT INTO endpoint_status(kind,last_attempt,last_success,status,message)
               VALUES(?,?,?,?,?) ON CONFLICT(kind) DO UPDATE SET
               last_attempt=excluded.last_attempt,last_success=excluded.last_success,
               status=excluded.status,message=excluded.message''',
               (kind,iso_now(),last_success,'ok' if ok else 'error',message[:180]))

def import_activity_file(db:sqlite3.Connection,path:Path) -> dict:
    content=json.loads(path.read_text(encoding='utf-8'))
    if isinstance(content,list):
        items=content
    elif isinstance(content,dict) and isinstance(content.get('activities'),list):
        items=content['activities']
    else:
        raise ValueError('JSON should be an array of Garmin activity summaries or a bundle with activities')
    added=updated=ignored=0
    for item in items:
        if not isinstance(item,dict):ignored+=1;continue
        change,new=insert_activity(db,item)
        if change: added+=int(new);updated+=int(not new)
        else:ignored+=1
    db.commit()
    return {'added':added,'updated':updated,'unchanged_or_invalid':ignored}
