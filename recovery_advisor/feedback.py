"""Feedback for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .paths import REPORTS
from .storage import iso_now
from .classification import query_range, activity_classifications
from .analysis import context_data
from .reporting import write_report, ai_response

def require_workout(db: sqlite3.Connection, activity_id: str) -> dict:
    """Validate that the local Garmin record is a confirmed workout."""
    import json
    if not str(activity_id).isdecimal() or int(activity_id) <= 0:
        raise ValueError('Activity ID must be a positive numeric Garmin ID')
    row=db.execute('SELECT payload FROM activities WHERE id=?',(str(activity_id),)).fetchone()
    if row is None:
        raise ValueError('Workout not stored locally; run `recovery-advisor sync` first')
    activity=json.loads(row['payload'])
    classification=activity_classifications(db,[activity])
    if not classification or classification[0][1]['label']!='workout':
        raise ValueError('Activity is not confirmed as a workout; review its classification')
    return activity


def infer_workout(db: sqlite3.Connection, event_day: dt.date) -> str | None:
    """Auto-link only when that day contains exactly one confirmed workout."""
    matches=[str(item['activityId']) for item, result in
             activity_classifications(db,query_range(db,event_day,event_day))
             if result['label']=='workout']
    return matches[0] if len(matches)==1 else None


def record_feedback(db: sqlite3.Connection, question: str, event_day: dt.date,
                    activity_id: str | None = None) -> int:
    """Persist the athlete's question as reported experience, even if AI is disabled/fails."""
    question=question.strip()
    if not question:
        raise ValueError('Question / feedback must not be empty')
    if len(question)>2000:
        raise ValueError('Question / feedback must be 2000 characters or shorter')
    # A missing activity association is permissible; never guess among multiple
    # workouts from the same day.
    if activity_id is not None:
        require_workout(db,activity_id)
    cursor=db.execute('INSERT INTO feedback(received_at,event_day,question,activity_id) VALUES(?,?,?,?)',
                      (iso_now(), event_day.isoformat(), question,activity_id))
    db.commit()
    return cursor.lastrowid

def ask(db:sqlite3.Connection,today:dt.date,config:dict,
        question:str|None=None,event_day:dt.date|None=None,activity_id:str|None=None) -> Path:
    if question is None:
        question=input('Question about training, recovery, nutrition or symptoms: ').strip()
    event_day=event_day or today
    fid=record_feedback(db,question,event_day,activity_id)
    # Rebuild the local weekly report to reflect new feedback immediately.
    write_report(db,today,config)
    # Recording happens before any network call, so an unsuccessful AI request
    # never discards valuable symptom/performance observations.
    answer=ai_response(context_data(db,today,config),question,config['openai_model'])
    db.execute('UPDATE feedback SET answer=? WHERE id=?',(answer,fid))
    db.commit()
    REPORTS.mkdir(parents=True,exist_ok=True)
    path=REPORTS/f'{today.isoformat()}_answer_{fid}.md'
    path.write_text('# Recovery Advisor Answer\n\n'+answer+'\n',encoding='utf-8')
    return path

def read_score(message:str) -> int:
    value=input(message).strip()
    if not value.isdecimal() or not 0<=int(value)<=10:
        raise ValueError('Score must be an integer between 0 and 10')
    return int(value)

def checkin(db:sqlite3.Connection,today:dt.date) -> None:
    print('0 = none / fully recovered; 10 = severe. For non-emergency recovery tracking only.')
    fatigue=read_score('Overall fatigue (0–10): ')
    soreness=read_score('Muscle soreness (0–10): ')
    energy=input('Energy (low/normal/high): ').strip().lower()
    if energy not in ('low','normal','high'):
        raise ValueError('Energy must be low, normal or high')
    symptoms=input('Symptoms or unusual sensations (optional): ').strip()[:500]
    food=input('Recovery meal / hydration note (optional): ').strip()[:500]
    db.execute('''INSERT INTO checkins(day,fatigue,soreness,energy,symptoms,food_notes,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET
              fatigue=excluded.fatigue,soreness=excluded.soreness,energy=excluded.energy,
              symptoms=excluded.symptoms,food_notes=excluded.food_notes,updated_at=excluded.updated_at''',
              (today.isoformat(),fatigue,soreness,energy,symptoms,food,iso_now()))
    db.commit()
