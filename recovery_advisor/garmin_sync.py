"""Garmin Sync for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .storage import (activity_date, insert_activity, upsert_detail, insert_wellness,
    insert_garmin_notes, state_get, state_set, status_put, iso_now)

def error_label(error:Exception) -> str:
    status=getattr(error,'status_code',None)
    if status==429: return 'Garmin rate limit (HTTP 429); no automatic retries'
    if status in (401,403): return f'Garmin authorization failed (HTTP {status}); check API key/token'
    if isinstance(status,int): return f'Garmin API HTTP {status}'
    if type(error).__name__ == 'GarminPersonalApiError':
        return 'Garmin authentication/setup error; check your Personal API key and its expiry'
    return type(error).__name__  # no URLs, tokens or private response payloads in logs

def fetch_activity_pages(client:Any, first:dt.date,last:dt.date,page_size:int=50) -> list[dict]:
    result=[]
    seen=set()
    for page in range(100):
        batch=client.search_activities(start=page*page_size,limit=page_size,
                  start_date=first.isoformat(),end_date=(last+dt.timedelta(days=1)).isoformat())
        if not isinstance(batch,list):
            raise RuntimeError('Garmin activity search returned an unexpected payload type')
        for item in batch:
            if not isinstance(item,dict):continue
            day=activity_date(item)
            if day and first.isoformat()<=day<=last.isoformat():
                key=str(item.get('activityId',''))
                if key not in seen:
                    seen.add(key)
                    result.append(item)
        if len(batch)<page_size:
            return result
    raise RuntimeError('Activity pagination limit reached; nothing further fetched')

def endpoint_start(db: sqlite3.Connection, kind: str, today: dt.date, config: dict,
                   backfill_days: int | None = None) -> dt.date:
    """Scan from the last successful endpoint sync day (inclusive), not a fixed lookback.

    Failed endpoints retain their previous checkpoint. An upgrade from the old
    lookback-based version has no checkpoint and automatically backfills 30 days.
    """
    baseline_first = today - dt.timedelta(days=max(1, int(config['baseline_days'])) - 1)
    checkpoint = state_get(db, f'last_success_day_{kind}')
    if not checkpoint:
        first=baseline_first
    else:
        try: first=dt.date.fromisoformat(checkpoint)
        except ValueError: first=baseline_first
    # The inclusive day catches uploads later on the previous sync day.
    # An explicit manual backfill can recover older late-arriving Garmin uploads.
    first=min(today, first)
    if backfill_days:
        first=min(first,today-dt.timedelta(days=backfill_days-1))
    return first

def advance_checkpoint(db: sqlite3.Connection, kind: str, today: dt.date) -> None:
    state_set(db, f'last_success_day_{kind}', today.isoformat())
    state_set(db, f'last_success_at_{kind}', iso_now())

def sync_with_client(db:sqlite3.Connection,client:Any,today:dt.date,config:dict,
                     details:bool=True,backfill_days:int|None=None,refresh_notes:bool=False) -> dict:
    first = endpoint_start(db, 'activities', today, config,backfill_days)
    if not state_get(db,'v21_note_detail_recheck'):
        # v2 stored activity details without text; queue the existing 30-day
        # details for safe refetch, retaining all summarized workout data.
        baseline_first=today-dt.timedelta(days=int(config['baseline_days'])-1)
        db.execute('UPDATE activities SET detail=NULL,detail_digest=NULL WHERE day>=?',(baseline_first.isoformat(),))
        state_set(db,'v21_note_detail_recheck',iso_now())
        db.commit()
    if refresh_notes:
        refresh_first=today-dt.timedelta(days=(backfill_days or int(config['baseline_days']))-1)
        db.execute('UPDATE activities SET detail=NULL,detail_digest=NULL WHERE day>=?',(refresh_first.isoformat(),))
        db.commit()
    initialized = state_get(db, 'last_success_day_activities') is not None
    events=[]
    try:
        activities=fetch_activity_pages(client,first,today)
        # Do not advance cursor if a partial/failed activity search is returned.
        changed_ids=[]
        new_ids=[]
        for item in activities:
            changed,new=insert_activity(db,item)
            if changed: changed_ids.append(str(item['activityId']))
            if new: new_ids.append(str(item['activityId']))
        advance_checkpoint(db,'activities',today)
        state_set(db,'garmin_initial_sync_done','yes')  # compatibility marker
        status_put(db,'activities',True,f'{len(activities)} records returned')
        db.commit()
    except Exception as exc:
        db.rollback()
        label=error_label(exc)
        status_put(db,'activities',False,label)
        db.execute('INSERT INTO runs(at,first_day,last_day,status,changed,message) VALUES(?,?,?,?,?,?)',
                   (iso_now(),first.isoformat(),today.isoformat(),'failed',0,label))
        db.commit()
        return {'status':'failed','changed':0,'period':[str(first),str(today)],'errors':[label]}

    wellness_changes=0
    detail_changes=0
    rate_limited=False
    if details:
        # Retry any missing details, including older entries left pending by an outage.
        # Cap requests to avoid an unbounded backlog during a scheduled sync.
        missing_ids=[r['id'] for r in db.execute(
            'SELECT id FROM activities WHERE detail IS NULL ORDER BY day DESC LIMIT 50')]
        # Garmin descriptions may be edited after the workout summary itself was
        # fetched. Recheck details from today and yesterday at the two scheduled runs.
        two_days_ago=(today-dt.timedelta(days=1)).isoformat()
        recent_ids=[r['id'] for r in db.execute(
            'SELECT id FROM activities WHERE day >= ? ORDER BY day DESC LIMIT 25',(two_days_ago,))]
        detail_ids=list(dict.fromkeys(changed_ids+missing_ids+recent_ids))[:50]
        for aid in detail_ids:
            try:
                detail=client.get_activity(aid)
                detail_changes+=int(upsert_detail(db,aid,detail))
            except Exception as exc:
                events.append(f'activity_detail:{error_label(exc)}')
                if getattr(exc,'status_code',None)==429:
                    rate_limited=True
                    break
        status_put(db,'activity_details',not any(e.startswith('activity_detail:') for e in events),
                   f'{len(detail_ids)} detail requests')
    endpoint_windows={}
    endpoints=(('daily','get_daily_summaries'),('sleep','get_daily_sleep_summaries'),
               ('hrv','get_daily_hrv_summaries'),('body_battery','get_daily_body_battery'))
    for kind, method in endpoints:
        if rate_limited: break
        wellness_first=endpoint_start(db,kind,today,config,backfill_days)
        endpoint_windows[kind]=[str(wellness_first),str(today)]
        try:
            data=getattr(client,method)(wellness_first.isoformat(),today.isoformat())
            wellness_changes+=insert_wellness(db,kind,data,wellness_first,today)
            advance_checkpoint(db,kind,today)  # only after successful response + parsing
            status_put(db,kind,True,'response received; individual metric coverage may be missing')
        except Exception as exc:
            label=error_label(exc)
            events.append(f'{kind}:{label}')
            status_put(db,kind,False,label)
            if getattr(exc,'status_code',None)==429:
                rate_limited=True
                break
    # Wellness activity notes are distinct from workouts; they are NEVER counted
    # as training sessions, but their text is available to the advisor.
    note_changes=0
    if not rate_limited and callable(getattr(client,'get_wellness_activity_summaries',None)):
        kind='garmin_notes'
        notes_first=endpoint_start(db,kind,today,config,backfill_days)
        endpoint_windows[kind]=[str(notes_first),str(today)]
        try:
            response=client.get_wellness_activity_summaries(notes_first.isoformat(),today.isoformat())
            note_changes=insert_garmin_notes(db,response,notes_first,today)
            advance_checkpoint(db,kind,today)
            status_put(db,kind,True,f'{note_changes} note records changed')
        except Exception as exc:
            label=error_label(exc)
            events.append(f'garmin_notes:{label}')
            status_put(db,kind,False,label)
    effective_updates=len(changed_ids)+wellness_changes+detail_changes+note_changes
    report_needed=not initialized or effective_updates>0
    status='partial' if events else 'ok'
    db.execute('INSERT INTO runs(at,first_day,last_day,status,changed,message) VALUES(?,?,?,?,?,?)',
               (iso_now(),str(first),str(today),status,effective_updates,'; '.join(events)[:300]))
    db.commit()
    return {'status':status,'period':[str(first),str(today)],'endpoint_windows':endpoint_windows,
            'activities_found':len(activities),'new_activities':len(new_ids),
            'updated_activities':len(changed_ids)-len(new_ids),
            'changed':effective_updates,'garmin_notes_updated':note_changes,'wellness_days_updated':wellness_changes,
            'errors':events,'force_report':report_needed}

def sync_online(db:sqlite3.Connection,today:dt.date,config:dict,backfill_days:int|None=None,refresh_notes:bool=False) -> dict:
    try:
        from garmin_personal_api import (GarminPersonalApiClient,PersonalApiAuthenticator,resolve_api_key)
    except ImportError as exc:
        raise RuntimeError('Install dependencies: py -m pip install -r requirements.txt') from exc
    # resolve_api_key prefers GARMIN_PERSONAL_API_KEY or per-user key file; never print it.
    with PersonalApiAuthenticator() as auth:
        token=auth.obtain_token(resolve_api_key())
    with GarminPersonalApiClient(token.access_token) as client:
        return sync_with_client(db,client,today,config,backfill_days=backfill_days,refresh_notes=refresh_notes)
