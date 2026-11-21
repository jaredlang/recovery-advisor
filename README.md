# Endurance Advisor MVP — v2.2

Local-first Garmin training and recovery assistant for **amateur endurance athletes**. A scheduled collector syncs Garmin data at **10:00 a.m. and 7:00 p.m.**, keeps a **rolling 30-day baseline**, analyzes the **last seven days**, and produces a rule-based report plus optional AI interpretation. Questions about unusual performance, discomfort, or recovery become stored athlete feedback automatically. Daily check-ins are **optional**.

Designed for **Windows + Python 3.12+**. The code and tests run offline; live Garmin and OpenAI calls must be tested with your credentials on your computer.

## What's new in v2

| Behavior | Previous MVP | v2 |
| --- | --- | --- |
| Baseline | 14 days | **30 days** |
| Scheduled Garmin activity sync | Today + yesterday | **From last successful activity-sync date to today, inclusive** |
| Failed sleep, HRV, daily or Body Battery sync | Possibly skipped on later sync | **Each endpoint has a separate last-successful checkpoint and catches up independently** |
| Long-late Garmin uploads | No explicit reconciliation | Manual `sync --backfill-days 30` |
| Questions about symptoms/performance | Answer only | **Saved as dated athlete feedback before the AI call** |
| User-entered resting heart rate | Not supported | `resting_hr_bpm` setting |
| User's usual sleep times | Not supported | `usual_bedtime`, `usual_wake_time` settings |
| AI review | Frequent missing-data caveats | **Distinguishes Garmin observations, user settings, calculated baselines and labelled assumptions** |

## v2.2: Automatic classification of Garmin feedback-only entries

**No activity IDs are required in `settings.json`.** The app automatically classifies Garmin activity records using their actual sport type, name, description/notes, recorded exercise telemetry, duration/distance, and manual-entry flag when present. Classification is recalculated from local data whenever the report runs—no extra Garmin requests or AI calls are required for this step.

| Garmin entry | Default handling |
| --- | --- |
| Running, cycling, swimming, or another recognized activity type (even with a symptom note) | **Workout:** included in training totals; text also analyzed as feedback |
| Explicit journal/recovery/symptom entry under an unrecognized/Other type with no recorded exercise telemetry | **Feedback-only:** excluded from training totals; its note is analyzed |
| Generic/unclear Other activity | **Ambiguous:** counted provisionally, surfaced in the report and `review-activities` command; never silently removed |

The API does **not** provide a universal notes-only flag. Automatic classification is a conservative heuristic, **not guaranteed** to identify every note-only entry. For example, a generic `Other` activity showing 57 minutes/9.8 km may need confirmation; the app cannot know whether that was a real workout from duration/distance alone. Recognized workout types with actual sensor data take precedence over symptom wording, preventing a normal run with notes from disappearing from the training record.

See the classifications without editing any settings:

```powershell
.\.venv\Scripts\python.exe advisor.py activities --days 30
.\.venv\Scripts\python.exe advisor.py review-activities --days 30
```

Only if an entry is genuinely ambiguous or classified incorrectly, confirm it once:

```powershell
.\.venv\Scripts\python.exe advisor.py classify 123456789 feedback_only
.\.venv\Scripts\python.exe advisor.py classify 123456789 workout
.\.venv\Scripts\python.exe advisor.py classify 123456789 auto
```

The ID is illustrative and used **only** to correct an exception. Those optional decisions live in the SQLite `activity_classification_overrides` table, not `settings.json`. Existing v2.1 `notes_only_activity_ids` values are migrated automatically into SQLite and the obsolete setting is removed on first launch. This preserves prior confirmations while requiring **no manual ID list going forward**. The older `mark-feedback` and `unmark-feedback` commands remain compatibility aliases for confirming an exception.

**Important:** When an activity is provisionally counted as ambiguous, its workout totals remain visible but the AI is instructed to qualify them. The experimental recovery score does not treat the absence of athlete notes as a positive signal when ambiguous Garmin entries may hide feedback.

## v2.1 capabilities retained: Garmin notes and recovery context score

**Garmin feedback content:** Activity summaries/details may include `description`; wellness-activity summaries may contain `notes`. The collector retains text fields but excludes profile IDs and GPS metadata. Activity details also preserve Garmin self-evaluation effort (`directWorkoutRpe`) and feeling (`directWorkoutFeel`) when available. Written observations are sent to the AI provider only when AI analysis is enabled.

**Recovery score:** The report shows an **experimental 0–100 recovery context index** with evidence and low/medium confidence. It is not a validated training-readiness or medical score. Its reference value of 70 is arbitrary; an issues-only reporting convention with no recorded concerns and no ambiguous records adds at most +2 points. Recovery measurements, when available, influence the score.

**Technical warnings:** API status codes stay in the `status` command; athlete-facing reports only mention problems affecting the interpretation.

**Upgrade steps:** Disable the previous task, back up your `data/`, `settings.json` and `reports/` folders, extract v2.2 into a new folder, then copy your backed-up folders into it. Recreate `.venv` and run `advisor.py status`, `advisor.py review-activities --days 30`, `advisor.py report` and `advisor.py sync`. Existing sync checkpoints and recorded workouts are retained. On first launch, the deprecated notes-only ID list is moved to SQLite and removed from `settings.json`, preserving your edited weight and race goal. Restart the twice-daily schedule only after confirming the new installation.

If a Garmin note was edited after the last successful sync and falls outside the normal recent-day detail checks, manually refresh descriptions:

```powershell
.\.venv\Scripts\python.exe advisor.py sync --backfill-days 30 --refresh-notes
```

This makes extra Garmin requests, so reserve it for occasional reconciliation. The unit tests use simulated Garmin data; test classification of your actual Garmin note entries locally before relying on training totals.

## Project setup

Extract the ZIP to a writable directory such as `C:\Users\YOU\endurance_advisor_mvp_v2_2`. From PowerShell **inside the extracted project directory**:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Store your Garmin Personal API key in the official client's per-user key file:

```powershell
New-Item -ItemType Directory -Force "$HOME\.config\garmin" | Out-Null
$secret = Read-Host "Garmin Personal API key" -AsSecureString
$plaintext = [System.Net.NetworkCredential]::new('', $secret).Password
[System.IO.File]::WriteAllText("$HOME\.config\garmin\connect-personal-api-key", $plaintext)
Remove-Variable plaintext, secret
```

Or use `GARMIN_PERSONAL_API_KEY` as an environment variable. **Never put API keys in source code or `settings.json`.**

The application automatically creates `settings.json` on first launch. It includes:

```json
{
  "timezone": "America/Chicago",
  "baseline_days": 30,
  "analysis_days": 7,
  "resting_hr_bpm": null,
  "usual_bedtime": null,
  "usual_wake_time": null,
  "ai_reports_enabled": false,
  "openai_model": "gpt-4.1-mini",
  "weight_kg": null,
  "race_goal": "",
  "dietary_preferences": "",
  "feedback_reporting_mode": "issues_only"
}
```

**Set reference values only if they are genuinely representative of you.** Unknown values remain `null`; the AI is instructed not to fabricate them. For example, if you know your typical resting HR and sleep schedule:

```powershell
.\.venv\Scripts\python.exe advisor.py set resting_hr_bpm 52
.\.venv\Scripts\python.exe advisor.py set usual_bedtime 22:30
.\.venv\Scripts\python.exe advisor.py set usual_wake_time 06:30
```

**Those numbers are examples, not values inferred from your Garmin account.** To clear a reference, use `advisor.py set resting_hr_bpm clear` or `advisor.py set usual_bedtime clear`.

## Synchronization and data-gap behavior

```powershell
.\.venv\Scripts\python.exe advisor.py sync
.\.venv\Scripts\python.exe advisor.py status
```

- **First checkpoint-based sync:** Fetches the latest 30 calendar days, inclusive. Existing installations of the old MVP have no v2 checkpoint, so the first v2 run performs this backfill automatically.
- **Subsequent activity sync:** Reads `last_success_day_activities` in SQLite and searches from that date *inclusive* through the current local date. If the computer doesn't run for 5 days, the next successful run searches all the missed days, not merely a fixed two-day window.
- **Failed activity search:** Doesn't advance the activity cursor. The next run attempts the same uncovered interval.
- **Recovery sync:** Sleep, HRV, daily and Body Battery each track their own successful sync date/time. If HRV fails but activity search succeeds, HRV's next run still covers its missing period.
- **Detail retrieval:** New/changed records and up to 50 older missing activity details are fetched on a scheduled run; recent activity details are also rechecked for edited notes. New/changed summaries are upserted by Garmin activity ID; repeated syncs are idempotent.
- **Last-success timestamp:** The program records both UTC timestamp and local sync day for every successful endpoint. Because Garmin's search accepts day-based ranges, the collector uses the local *date* of the last successful sync, inclusive; it doesn't invent a more granular API query.
- **Late uploads predating a successful sync:** An activity recorded on an old date and uploaded only *after* that date's successful scan could still be missed. To reconcile the last 30 days on demand, run:

```powershell
.\.venv\Scripts\python.exe advisor.py sync --backfill-days 30
```

That backfill scans activities and recovery metrics for the requested span without clearing the database or creating duplicates. It intentionally makes more API calls. Two daily scheduled syncs minimize normal calls but do **not** constitute an unlimited Garmin API allowance.

If Garmin returns 429, the program stops making additional wellness calls in that run, retains successful checkpoints and reports partial status. If your watch hasn't uploaded an activity to Garmin, the app cannot retrieve it.

## Report, questions and feedback

```powershell
.\.venv\Scripts\python.exe advisor.py report
.\.venv\Scripts\python.exe advisor.py ask
.\.venv\Scripts\python.exe advisor.py feedback
```

- `report`: Regenerates the latest deterministic report without accessing Garmin. When AI reports are enabled, also generates an AI review.
- `ask`: Prompts for your question and **saves it as dated athlete feedback before requesting an AI answer**. Even if the OpenAI call fails or no key is configured, the observation remains in the SQLite database. The deterministic weekly report refreshes immediately. Answers are saved separately as `reports/YYYY-MM-DD_answer_ID.md` rather than overwriting previous answers.
- `feedback`: Saves a performance/symptom observation to the weekly report without requiring an AI API key.
- Both `ask` and `feedback` accept `--event-day YYYY-MM-DD` for an observation that actually occurred earlier; default is today. Questions and observations are surfaced in the **7-day analysis**. A feedback entry represents what you reported, not an independently confirmed diagnosis.
- `checkin`: Optional structured fatigue/soreness, energy and meal notes. **No daily check-in is required** and the advisor should not assume normal health simply because none was submitted.

Examples:

```powershell
.\.venv\Scripts\python.exe advisor.py ask
.\.venv\Scripts\python.exe advisor.py feedback --event-day 2026-11-20
.\.venv\Scripts\python.exe advisor.py set race_goal "Half-distance triathlon"
.\.venv\Scripts\python.exe advisor.py set weight_kg 70.3
```

### How reference assumptions work

The analysis context contains `baseline_references`, which labels each reference by provenance:

1. If `resting_hr_bpm` is set, it is a **user-provided reference**, not an observed heart-rate measurement for that morning.
2. Otherwise, if at least five valid Garmin daily resting-HR readings exist within 30 days, their **median** becomes a **calculated** provisional resting-HR reference. If neither condition holds, the advisor does **not** invent a numeric value.
3. If usual bedtime and wake time are configured, their difference indicates **possible sleep opportunity**, not measured sleep or proof of recovery.
4. If five or more valid Garmin sleep-duration records exist, the baseline includes the **median recorded sleep duration** with its sample count.
5. An absence of reports is a weak favorable signal *only* when `feedback_reporting_mode` is `issues_only`; it never proves normal recovery.

Both the deterministic report and AI review are directed to state these assumptions explicitly. The AI should provide practical, conditional training/nutrition guidance without repeatedly listing irrelevant missing fields. A 30-day history is **not** a medically validated recovery baseline.

### Optional OpenAI analysis

The default deterministic report works without OpenAI. To enable conversational analysis and AI review, set your `OPENAI_API_KEY` in the Windows environment and opt in:

```powershell
$secret = Read-Host "OpenAI API key" -AsSecureString
$plaintext = [System.Net.NetworkCredential]::new('', $secret).Password
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $plaintext, 'User')
$env:OPENAI_API_KEY = $plaintext
Remove-Variable plaintext, secret
.\.venv\Scripts\python.exe advisor.py set ai_reports_enabled true
.\.venv\Scripts\python.exe advisor.py report
```

OpenAI API billing is separate. The app calls OpenAI with `store=False`, sends a minimized seven-day activity/30-day wellness context plus relevant symptom questions, and never transmits Garmin API credentials or GPS/profile IDs. Nevertheless, symptom questions and wellness summaries are **sensitive personal health information**; use this feature only if you're comfortable sharing it with the API provider.

## Windows Task Scheduler

After a manual `sync` works, install the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
Get-ScheduledTask -TaskName EnduranceAdvisor-GarminSync
```

It runs once at 10 AM and once at 7 PM **Windows local time**, under your signed-in account. Match your OS time zone with `timezone` in settings. The task can catch up when Windows next runs it, but isn't guaranteed to execute while the PC is off or the account is signed out. To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_schedule.ps1
```

## Upgrade from the original MVP without losing history

1. **Uninstall or disable the old scheduled task first** to avoid two collectors running simultaneously.
2. **Back up** your old `data/`, `settings.json` and `reports/` (all are local files; no cloud migration is needed).
3. Extract v2 into a **new folder**. Copy your existing `data/` and `settings.json` into that folder. You may also copy `reports/` if you want the historical Markdown reports.
4. Create the virtual environment and run `advisor.py status`. The app adds the new classification-override table when opening the existing SQLite database. A legacy config with `daily_sync_lookback_days` will be migrated, and the previous shipped 14-day baseline moves to 30 days. Other personal settings are preserved.
5. Run `advisor.py sync` manually: absent v2 checkpoint metadata triggers a fresh 30-day reconciliation without duplicating existing Garmin activity IDs.
6. Install the v2.2 schedule in the **new** directory. Do not install two running copies pointed at the same Garmin credentials.

## Offline tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe advisor.py --as-of 2026-11-08 import-json examples\sample_activities.json
.\.venv\Scripts\python.exe advisor.py --as-of 2026-11-08 report
```

The tests cover missing-day catch-up, endpoint-specific failed requests, older activity details, manual backfill, feedback persistence, baseline assumptions, privacy allowlists and idempotent imports. They do not test actual Garmin authentication or the live OpenAI API.

## File and privacy notes

- SQLite: `data/advisor.sqlite3`. Reports: `reports/`. Settings: `settings.json`.
- `.gitignore` excludes local data, keys, reports and settings. API credentials are **not** copied into the project.
- `sync` is the only CLI command that contacts Garmin. Both `ask` and `report` read local data.
- The model is not a medical provider. Persistent or worsening symptoms merit medical evaluation; chest pain, fainting, severe breathing difficulty, or new neurological problems require urgent medical care.
- If the user requests no AI calls, leave `ai_reports_enabled` false and don't run `ask`.

Garmin API links: https://github.com/garmin/connect-personal-api-python · https://developer.garmin.com/gc-developer-program/personal/swagger-docs/
