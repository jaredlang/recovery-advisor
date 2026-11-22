"""Feedback for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .paths import REPORTS
from .storage import iso_now
from .analysis import context_data
from .reporting import write_report, ai_response

def record_feedback(db: sqlite3.Connection, question: str, event_day: dt.date) -> int:
    """Persist the athlete's question as reported experience, even if AI is disabled/fails."""
    question=question.strip()
    if not question:
        raise ValueError('Question / feedback must not be empty')
    if len(question)>2000:
        raise ValueError('Question / feedback must be 2000 characters or shorter')
    cursor=db.execute('INSERT INTO feedback(received_at,event_day,question) VALUES(?,?,?)',
                      (iso_now(), event_day.isoformat(), question))
    db.commit()
    return cursor.lastrowid

def ask(db:sqlite3.Connection,today:dt.date,config:dict,
        question:str|None=None,event_day:dt.date|None=None) -> Path:
    if question is None:
        question=input('Question about training, recovery, nutrition or symptoms: ').strip()
    event_day=event_day or today
    fid=record_feedback(db,question,event_day)
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
