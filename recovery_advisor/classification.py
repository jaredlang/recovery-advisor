"""Classification for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any

from .storage import activity_date, iso_now, to_float

# Rules are deliberately strict: the Garmin API has no universal "notes only"
# indicator. Reclassify automatically whenever an activity or its details change.
# Human corrections are rare exceptions persisted in SQLite, never settings.json.
NOTE_TITLE = re.compile(
    r'\b(?:feedback(?:\s+only)?|(?:training|recovery|wellness|symptom)\s+notes?'
    r'|notes?\s+only|journal\s+entry|diary\s+entry|no\s+workout)\b', re.I)
SYMPTOM = re.compile(
    r'\b(?:sore(?:ness)?|pain(?:ful)?|aching?|fatigu(?:ed|e)|tired|exhausted'
    r'|heavy\s+legs?|dizzi(?:ness|y)|nausea|headache|cramp(?:s|ing)?'
    r'|felt\s+unwell|sick|illness)\b', re.I)
SYMPTOM_CONTEXT = re.compile(
    r'\b(?:after|before|during|following|today|yesterday|this\s+morning'
    r'|last\s+night|woke|felt|my|legs?|muscles?)\b', re.I)
NON_EXERCISE_TYPES = {'other','uncategorized','unknown','generic','miscellaneous','note','notes',''}
TELEMETRY_KEYS = ('averageHR','maxHR','avgPower','normPower',
                  'averageRunningCadenceInStepsPerMinute',
                  'averageBikingCadenceInRevPerMinute','trainingStressScore',
                  'hrTimeInZone_1','hrTimeInZone_2','hrTimeInZone_3',
                  'hrTimeInZone_4','hrTimeInZone_5')

def sport(kind:str) -> str:
    k=str(kind or '').lower()
    if 'run' in k: return 'running'
    if any(v in k for v in ('ride','cycling','bike','biking')): return 'cycling'
    if 'swim' in k: return 'swimming'
    return 'other'

def classification_overrides(db: sqlite3.Connection) -> dict[str, str]:
    return {row['activity_id']: row['label'] for row in db.execute(
        'SELECT activity_id,label FROM activity_classification_overrides')}

def set_classification_override(db: sqlite3.Connection, activity_id: str,
                                label: str | None, origin: str = 'user_confirmation') -> None:
    if not str(activity_id).isdecimal():
        raise ValueError('Activity ID must be numeric')
    if label is None or label == 'auto':
        db.execute('DELETE FROM activity_classification_overrides WHERE activity_id=?',
                   (str(activity_id),))
    elif label in ('workout','feedback_only'):
        db.execute("""INSERT INTO activity_classification_overrides(activity_id,label,origin,updated_at)
                   VALUES(?,?,?,?) ON CONFLICT(activity_id) DO UPDATE SET
                   label=excluded.label,origin=excluded.origin,updated_at=excluded.updated_at""",
                   (str(activity_id),label,origin,iso_now()))
    else:
        raise ValueError('Classification must be workout, feedback_only or auto')

def classify_activity(activity: dict, override: str | None = None) -> dict:
    """Conservative, deterministic classification. No API/LLM calls.

    Ambiguous entries remain provisionally counted as workouts, but appear in
    review output. Explicit notes-like titles without exercise telemetry can
    be classified despite Garmin's sometimes misleading duration/distance.
    """
    if override in ('workout','feedback_only'):
        return {'label':override, 'source':'confirmed',
                'reason':'Previously confirmed classification stored in SQLite'}
    kind = str(activity.get('activityType',{}).get('typeKey','')).lower()
    title = str(activity.get('activityName') or '').strip()
    description = str(activity.get('description') or activity.get('notes') or '').strip()
    has_telemetry = any((to_float(activity.get(k)) or 0) > 0 for k in TELEMETRY_KEYS)
    manual = activity.get('isManualActivity') is True or activity.get('manualActivity') is True
    duration = to_float(activity.get('duration')) or 0
    distance = to_float(activity.get('distance')) or 0
    has_load = duration >= 480 or distance >= 500
    note_title = bool(NOTE_TITLE.search(title)) or title.casefold() in (
        'note','notes','my notes','journal','diary')
    symptom_title = bool(SYMPTOM.search(title) and SYMPTOM_CONTEXT.search(title))
    if kind not in NON_EXERCISE_TYPES:
        return {'label':'workout','source':'automatic',
                'reason':'Recognized exercise type; attached notes are retained as observations'}
    if has_telemetry:
        return {'label':'workout','source':'automatic',
                'reason':'Recorded exercise telemetry; attached notes are retained as observations'}
    if note_title or symptom_title:
        return {'label':'feedback_only','source':'automatic',
                'reason':'Note/symptom-style activity title with no exercise telemetry'}
    if description and not has_load:
        return {'label':'feedback_only','source':'automatic',
                'reason':'Written observation with no substantive exercise measurements'}
    if has_load and title and title.casefold() not in ('other','activity','unknown','uncategorized'):
        if manual and description:
            return {'label':'ambiguous','source':'automatic',
                    'reason':'Manual Other-type activity has duration and a note; could be exercise'}
        return {'label':'workout','source':'automatic',
                'reason':'Substantive duration/distance with a non-note activity title'}
    return {'label':'ambiguous','source':'automatic',
            'reason':'Generic/unknown activity type with insufficient evidence of its purpose'}

def activity_classifications(db: sqlite3.Connection, items: list[dict]) -> list[tuple[dict,dict]]:
    overrides = classification_overrides(db)
    return [(a,classify_activity(a,overrides.get(str(a.get('activityId',''))))) for a in items]

def query_range(db:sqlite3.Connection,first:dt.date,last:dt.date) -> list[dict]:
    rows=db.execute('SELECT payload,detail FROM activities WHERE day BETWEEN ? AND ? ORDER BY start_local',
                    (first.isoformat(),last.isoformat())).fetchall()
    result=[]
    for r in rows:
        activity=json.loads(r['payload'])
        detail=json.loads(r['detail']) if r['detail'] else {}
        for key in ('description','notes'):
            if isinstance(detail.get(key),str) and detail[key].strip():
                activity[key]=detail[key][:2000]
        for key in ('directWorkoutRpe','directWorkoutFeel'):
            if to_float(detail.get(key)) is not None:
                activity[key]=detail[key]
        for key in ('isManualActivity','manualActivity'):
            if isinstance(detail.get(key),bool):
                activity[key]=detail[key]
        result.append(activity)
    return result
