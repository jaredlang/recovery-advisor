"""Analysis for the endurance recovery advisor."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict
from statistics import median
from typing import Any

from .storage import to_float, activity_date
from .classification import sport, query_range, activity_classifications

def summarize_activities(items:list[dict]) -> dict:
    groups=defaultdict(lambda:{'count':0,'duration_minutes':0.0,'distance_km':0.0})
    daily=defaultdict(lambda:{'count':0,'duration_minutes':0.0,'sports':[]})
    total_min=0.0
    for a in items:
        typ=sport(a.get('activityType',{}).get('typeKey',''))
        minutes=(to_float(a.get('duration')) or 0)/60
        distance=(to_float(a.get('distance')) or 0)/1000
        day=activity_date(a)
        groups[typ]['count']+=1
        groups[typ]['duration_minutes']+=minutes
        groups[typ]['distance_km']+=distance
        total_min+=minutes
        if day:
            daily[day]['count']+=1
            daily[day]['duration_minutes']+=minutes
            daily[day]['sports'].append(typ)
    result={k:{m:round(v,2) if isinstance(v,float) else v for m,v in d.items()}
            for k,d in groups.items()}
    return {'activities':len(items),'duration_minutes':round(total_min,1),
            'sports':result,'days':dict(daily)}

def baseline_references(config: dict, wellness: list[dict], today: dt.date) -> dict:
    """Create explicit, source-labelled reference points. Never fill missing readings."""
    from_garmin=[]
    recent_rhr=[]
    sleep_hours=[]
    for row in wellness:
        metric, values = row['metric'], row['values']
        if metric=='daily':
            hr=to_float(values.get('restingHeartRate'))
            if hr is not None and 30<=hr<=110:
                from_garmin.append(hr)
                if row['day']>=(today-dt.timedelta(days=6)).isoformat():
                    recent_rhr.append(hr)
        if metric=='sleep':
            secs=to_float(values.get('sleepTimeSeconds'))
            if secs is None: secs=to_float(values.get('sleepDuration'))
            if secs is not None and 2*3600<=secs<=16*3600:
                sleep_hours.append(secs/3600)
    declared=to_float(config.get('resting_hr_bpm'))
    reference=None
    assumptions=[]
    if declared is not None and 30<=declared<=110:
        reference={'bpm':round(declared,1),'source':'user_setting', 'days_observed':0}
        assumptions.append(f'Resting HR reference {declared:g} bpm is user-provided in settings; it is not a measured reading for today.')
    elif len(from_garmin)>=5:
        value=round(median(from_garmin),1)
        reference={'bpm':value,'source':'garmin_30_day_median','days_observed':len(from_garmin)}
        assumptions.append(f'Resting HR reference {value:g} bpm is the median of {len(from_garmin)} Garmin daily readings within the rolling baseline, not an individual medical threshold.')
    else:
        assumptions.append('No reliable resting HR reference was available; no resting HR value has been assumed.')
    bedtime, wake=config.get('usual_bedtime'),config.get('usual_wake_time')
    sleep_schedule=None
    if bedtime and wake:
        try:
            b=dt.time.fromisoformat(bedtime)
            w=dt.time.fromisoformat(wake)
            minutes=(w.hour*60+w.minute-b.hour*60-b.minute)%(24*60)
            if not (180<=minutes<=900): raise ValueError('implausible sleep opportunity')
            sleep_schedule={'usual_bedtime':bedtime,'usual_wake_time':wake,
                            'planned_sleep_opportunity_hours':round(minutes/60,1),
                            'source':'user_setting'}
            assumptions.append(f'Usual {bedtime}–{wake} sleep schedule implies about {minutes/60:.1f} hours in bed, NOT observed sleep duration or sleep quality.')
        except (ValueError,TypeError):
            assumptions.append('Sleep schedule settings are invalid, so no sleep opportunity is estimated.')
    if len(sleep_hours)>=5:
        assumptions.append(f'Observed Garmin sleep duration reference is a {len(sleep_hours)}-night median, not a guarantee of nightly recovery.')
    elif not sleep_schedule:
        assumptions.append('No sleep schedule or sufficient Garmin sleep duration exists; no sleep amount has been assumed.')
    assumptions.append('A missing symptom report does not mean the athlete was symptom-free; questions are treated as observations, not diagnoses.')
    return {'resting_hr_reference':reference,
            'recent_resting_hr_mean_bpm':round(sum(recent_rhr)/len(recent_rhr),1) if recent_rhr else None,
            'recent_resting_hr_days':len(recent_rhr),
            'usual_sleep_schedule':sleep_schedule,
            'garmin_sleep_median_hours':round(median(sleep_hours),1) if len(sleep_hours)>=5 else None,
            'garmin_sleep_nights':len(sleep_hours),
            'assumptions':assumptions}

def context_data(db:sqlite3.Connection,today:dt.date,config:dict) -> dict:
    analysis=max(1,int(config['analysis_days']))
    baseline=max(analysis,int(config['baseline_days']))
    analysis_first=today-dt.timedelta(days=analysis-1)
    baseline_first=today-dt.timedelta(days=baseline-1)
    previous_first=analysis_first-dt.timedelta(days=analysis)
    previous_last=analysis_first-dt.timedelta(days=1)
    all_current=query_range(db,analysis_first,today)
    classes=activity_classifications(db,all_current)
    marked_feedback=[item for item,decision in classes if decision['label']=='feedback_only']
    current=[item for item,decision in classes if decision['label']!='feedback_only']
    ambiguous=[{'day':activity_date(item),
                'original_activity_type':item.get('activityType',{}).get('typeKey','unknown'),
                'reason':decision['reason']} for item,decision in classes
               if decision['label']=='ambiguous']
    prior=[item for item,decision in activity_classifications(
        db,query_range(db,max(baseline_first,previous_first),previous_last))
           if decision['label']!='feedback_only'] if previous_last>=baseline_first else []
    historic=[item for item,decision in activity_classifications(
        db,query_range(db,baseline_first,analysis_first-dt.timedelta(days=1)))
              if decision['label']!='feedback_only']
    previous_days=(analysis_first-baseline_first).days
    daily=[]
    for row in db.execute('SELECT kind,day,payload FROM wellness WHERE day BETWEEN ? AND ? ORDER BY day',
                          (baseline_first.isoformat(),today.isoformat())):
        record=json.loads(row['payload'])
        daily.append({'metric':row['kind'],'day':row['day'],
                      'values':{k:v for k,v in record.items() if k!='calendarDate'}})
    checkins=[]
    for row in db.execute('SELECT day,fatigue,soreness,energy,symptoms,food_notes FROM checkins '
                          'WHERE day BETWEEN ? AND ? ORDER BY day',
                          (baseline_first.isoformat(),today.isoformat())):
        checkins.append(dict(row))
    questions=[]
    for row in db.execute('SELECT event_day,received_at,question,activity_id FROM feedback '
                          'WHERE event_day BETWEEN ? AND ? ORDER BY received_at,id',
                          (analysis_first.isoformat(),today.isoformat())):
        questions.append(dict(row))
    # Activity description and Garmin wellness notes may both carry the athlete's
    # own comments. Deduplicate equal day/text and preserve provenance.
    garmin_observations=[]
    seen_text=set()
    for item in marked_feedback:
        # Some people write their feedback in the activity name, not Description.
        # Preserve it only for an activity the athlete explicitly marked as notes-only.
        note=item.get('description') or item.get('notes') or item.get('activityName')
        if isinstance(note,str) and note.strip():
            token=(activity_date(item),note.strip().casefold())
            seen_text.add(token)
            garmin_observations.append({'day':activity_date(item),
                         'source':'garmin_feedback_only_activity', 'activity_id':str(item.get('activityId')), 'text':note.strip()[:2000]})
    for item in current:
        for field in ('description','notes'):
            value=item.get(field)
            if isinstance(value,str) and value.strip():
                token=(activity_date(item),value.strip().casefold())
                if token not in seen_text:
                    seen_text.add(token)
                    garmin_observations.append({'day':activity_date(item),'source':'activity_description', 'activity_id':str(item.get('activityId')),
                                                'sport':sport(item.get('activityType',{}).get('typeKey','')),
                                                'original_activity_type':item.get('activityType',{}).get('typeKey','unknown'),
                                                'text':value.strip()[:2000]})
    for row in db.execute('SELECT day,label,note FROM garmin_notes WHERE day BETWEEN ? AND ? ORDER BY day',
                          (analysis_first.isoformat(),today.isoformat())):
        token=(row['day'],row['note'].strip().casefold())
        if token not in seen_text:
            seen_text.add(token)
            garmin_observations.append({'day':row['day'],'source':'wellness_activity_note',
                                        'label':row['label'],'text':row['note'][:2000]})
    workouts=[]
    for item in current:
        workouts.append({
          'day':activity_date(item), 'activity_id':str(item.get('activityId')), 'sport':sport(item.get('activityType',{}).get('typeKey','')),
          'original_activity_type':item.get('activityType',{}).get('typeKey','unknown'),
          'minutes':round((to_float(item.get('duration')) or 0)/60,1),
          'distance_km':round((to_float(item.get('distance')) or 0)/1000,2),
          'avg_hr_bpm':item.get('averageHR'), 'max_hr_bpm':item.get('maxHR'),
          'avg_power_w':item.get('avgPower'),'normalized_power_w':item.get('normPower'),
          'training_stress_score':item.get('trainingStressScore'),
          'intensity_factor':item.get('intensityFactor'),
          'self_evaluation_rpe_0_to_10':round((to_float(item.get('directWorkoutRpe')) or 0)/10,1)
                     if item.get('directWorkoutRpe') is not None else None,
          'self_evaluation_feel_0_to_100':item.get('directWorkoutFeel')})
    statuses=[dict(r) for r in db.execute('SELECT kind,status,last_success,message FROM endpoint_status')]
    latest_run=db.execute('SELECT at,status,last_day,message FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    last_activity_status=next((e for e in statuses if e['kind']=='activities'),None)
    last_note_status=next((e for e in statuses if e['kind']=='garmin_notes'),None)
    impact=[]
    if (last_activity_status and last_activity_status['status']=='error') or \
       (latest_run and latest_run['status']=='failed'):
        impact.append('Latest Garmin activity synchronization did not complete; recent workouts may be missing.')
    if last_note_status and last_note_status['status']=='error':
        impact.append('Garmin wellness-activity notes could not be checked; activity descriptions may still be available.')
    retrieval_impact=' '.join(impact) if impact else None
    result={
      'as_of':today.isoformat(),
      'window':{'analysis_start':analysis_first.isoformat(),'analysis_end':today.isoformat(),
                'baseline_start':baseline_first.isoformat(),'baseline_end':today.isoformat(),
                'baseline_days':baseline,'analysis_days':analysis},
      'current':summarize_activities(current),
      'comparison':summarize_activities(prior),
      'baseline_training':{'historical_days':previous_days,
                           'historical_workouts':len(historic),
                           'weekly_equivalent_minutes':round(summarize_activities(historic)['duration_minutes']*7/previous_days,1) if previous_days else None},
      'garmin_observations':garmin_observations,
      'classification_review':ambiguous,
      'feedback_only_activity_count':len(marked_feedback),
      'recent_workouts':workouts,
      'daily_wellness_metrics':daily,
      'self_reports':checkins,
      'athlete_feedback':questions,
      'baseline_references':baseline_references(config,daily,today),
      'race_goal':config.get('race_goal') or 'not provided',
      'weight_kg':config.get('weight_kg'),
      'dietary_preferences':config.get('dietary_preferences') or 'not provided',
      'feedback_reporting_mode':config.get('feedback_reporting_mode','unspecified'),
      'feedback_notes_available':not (last_note_status and last_note_status['status']=='error') and not bool(ambiguous),
      'retrieval_impact':retrieval_impact,
      'sync_status':dict(latest_run) if latest_run else None,
      'endpoint_statuses':statuses,
      'limitations':['30 days is a provisional baseline, not proof of physiological norms',
                     'missing self-reports are not evidence of symptom-free days',
                     'unrecorded workouts, wearable gaps and late Garmin uploads are possible',
                     'Garmin activity calories are estimates, not exact energy or hydration needs']
    }
    result['recovery_score']=estimate_recovery(result)
    return result

def estimate_recovery(context:dict) -> dict:
    """Conservative, unvalidated recovery *context* index; never medical readiness."""
    score=70
    evidence=[]
    assumptions=[]
    w=context['window']
    recent=[r for r in context['daily_wellness_metrics'] if r['day']>=w['analysis_start']]
    rhr=[to_float(r['values'].get('restingHeartRate')) for r in recent if r['metric']=='daily']
    rhr=[v for v in rhr if v is not None and 30<=v<=110]
    reference=context['baseline_references']['resting_hr_reference']
    if reference and len(rhr)>=3:
        difference=sum(rhr)/len(rhr)-reference['bpm']
        if difference>=10: score-=10; evidence.append('Recent average resting HR is at least 10 bpm above the available reference (−10).')
        elif difference>=5: score-=5; evidence.append('Recent average resting HR is at least 5 bpm above the available reference (−5).')
        else: evidence.append('Measured resting HR is not substantially above its reference (no adjustment).')
    else:
        assumptions.append('Insufficient recent measured resting HR to adjust the score.')
    sleep=[to_float(r['values'].get('sleepTimeSeconds',r['values'].get('sleepDuration'))) for r in recent if r['metric']=='sleep']
    sleep=[v/3600 for v in sleep if v is not None and 2*3600<=v<=16*3600]
    sleep_baseline=context['baseline_references']['garmin_sleep_median_hours']
    if len(sleep)>=3 and sleep_baseline is not None:
        deficit=sleep_baseline-sum(sleep)/len(sleep)
        if deficit>=1.5: score-=10; evidence.append('Recent observed sleep is at least 1.5 h below observed baseline (−10).')
        elif deficit>=0.75: score-=5; evidence.append('Recent observed sleep is at least 0.75 h below observed baseline (−5).')
        else: evidence.append('Recent observed sleep is not substantially below baseline (no adjustment).')
    else:
        assumptions.append('Insufficient measured sleep to adjust the score; bedtime settings are not sleep observations.')
    base=context['baseline_training']
    prior_minutes=base.get('weekly_equivalent_minutes')
    if base['historical_days']>=14 and base['historical_workouts']>=4 and prior_minutes and prior_minutes>=60:
        ratio=context['current']['duration_minutes']/prior_minutes
        if ratio>=1.5: score-=8; evidence.append('Training time is at least 50% above the previous baseline weekly equivalent (−8).')
        elif ratio>=1.25: score-=4; evidence.append('Training time is at least 25% above the previous baseline weekly equivalent (−4).')
    else:
        assumptions.append('Prior training history is too sparse to score changes in training volume.')
    weak_eval=[r for r in context['recent_workouts'] if to_float(r.get('self_evaluation_feel_0_to_100')) is not None
               and to_float(r['self_evaluation_feel_0_to_100'])<=25]
    if weak_eval:
        score-=4
        evidence.append('At least one Garmin activity self-evaluation recorded a weak feeling during exercise (−4); this is not itself a recovery diagnosis.')
    checkins=[r for r in context['self_reports'] if r['day']>=w['analysis_start']]
    higher=[r for r in checkins if (r.get('fatigue') is not None and r['fatigue']>=7)
            or (r.get('soreness') is not None and r['soreness']>=7)]
    if higher:
        score-=8
        evidence.append('At least one high reported fatigue or soreness check-in (−8).')
    # Free text is not scored through naive symptom keyword matching; the AI
    # evaluates its meaning and context without assuming every question means pain.
    if context['athlete_feedback'] or context['garmin_observations']:
        evidence.append('Athlete comments are present; they require interpretation, not automatic negative scoring.')
    elif context.get('feedback_reporting_mode')=='issues_only' and context.get('feedback_notes_available',True):
        score+=2
        evidence.append('No concerns reported this week: weak positive signal under issues-only reporting convention (+2).')
        assumptions.append('Silence is not proof of recovery, and this adjustment is intentionally small.')
    else:
        assumptions.append('No positive adjustment for silence: reporting convention is unspecified or Garmin notes retrieval was incomplete.')
    evidence.append('Starting reference score 70 is arbitrary; this is an experimental index, not validated readiness.')
    confidence='medium' if len(rhr)>=3 and len(sleep)>=3 else 'low'
    return {'score':max(0,min(100,round(score))), 'scale':'0–100', 'confidence':confidence,
            'type':'experimental_recovery_context_index','validated':False,
            'evidence':evidence,'assumptions':assumptions}
