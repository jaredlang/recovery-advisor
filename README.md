# Recovery Advisor MVP v2.7

> An AI-powered endurance advisor that helps amateur athletes understand their body's response to training and make smarter decisions about recovery, nutrition and future workouts.

Recovery Advisor uses the **official Garmin Connect Personal API for reading** workout and available recovery data. It stores sanitized activity summaries, wellness measurements, athlete feedback and AI interpretations in local SQLite, and produces a rolling **7-day** report informed by a **30-day** baseline. Its personal API access is **read-only**; it never modifies Garmin activities or notes.

## What's new: fewer commands

Most days you only need these commands:

```powershell
recovery-advisor sync
recovery-advisor review
recovery-advisor ask "Why were my legs heavy after yesterday's ride?"
recovery-advisor status
```

| Command | What it does |
|---|---|
| `sync` | Incrementally retrieve Garmin records and refresh reports. Used by the scheduler. |
| `review` | Generate the latest recovery report from locally stored data. No Garmin request. |
| `ask` | Record the question as athlete feedback, then ask AI to explain the recent training/recovery context. Quoted text is optional; without it the program prompts. |
| `status` | Show sync checkpoints, stored records and recovery-data availability. |

`ask` always saves the question before requesting AI, so an AI failure does not lose an observation. It does invoke the configured AI model and requires an `OPENAI_API_KEY`; API usage has a cost. Nothing is sent to the AI by `review` unless `ai_reports_enabled` is on.

**No separate feedback entry is required for your usual workflow.** If you report a problem by asking about it, the question is already saved locally as feedback.

### Configuration

Display the current settings:

```powershell
recovery-advisor config
```

Change one setting at a time using short names:

```powershell
recovery-advisor config weight 70
recovery-advisor config hr 50
recovery-advisor config goal "Ironman 70.3"
recovery-advisor config bedtime 21:00
recovery-advisor config wake 04:30
recovery-advisor config diet "Chinese-based diet"
recovery-advisor config ai on
recovery-advisor config ai off
```

Also supported: `config baseline 30`, `config window 7`, and `config model MODEL_NAME`. Settings are stored in `settings.json`. Personal access tokens and API keys are **not** stored there.

### Occasional maintenance

Run `recovery-advisor manage` to see the maintenance menu. Examples:

```powershell
recovery-advisor manage workouts                   # list Garmin activities
recovery-advisor manage uncertain                  # ambiguous workout/notes-only entries
recovery-advisor manage classify ACTIVITY_ID feedback_only
recovery-advisor manage classify ACTIVITY_ID auto  # remove an override
recovery-advisor manage feedback                   # list saved feedback and IDs
recovery-advisor manage note "Legs felt heavy"      # save feedback without an AI request
recovery-advisor manage link FEEDBACK_ID ACTIVITY_ID
recovery-advisor manage unlink FEEDBACK_ID
recovery-advisor manage checkin
recovery-advisor manage backfill 30                 # older, late-uploaded activities
recovery-advisor manage backfill 30 --refresh-notes
recovery-advisor manage import path\to\activities.json
```

Most of these operations are local. `manage backfill` reads from Garmin, using existing authentication. `manage note` can optionally accept `--event-day YYYY-MM-DD` or `--activity-id ACTIVITY_ID`. Only link feedback to a workout when you're sure which workout it concerns; ambiguous same-day associations are not guessed.

**Backward compatibility:** All commands from v2.6 (including `report`, `feedback`, `feedback-list`, `set`, `activities`, `review-activities`, `classify`, `checkin`, `link-feedback`, `unlink-feedback`, `import-json`, and `sync --backfill-days`) still work. They are intentionally left out of top-level help to reduce clutter. Existing Task Scheduler jobs that call `sync` require no CLI changes.

## Windows installation

Extract the ZIP to a project folder. Open PowerShell there:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\Activate.ps1
recovery-advisor --version
recovery-advisor status
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again. Without activation use `.\.venv\Scripts\recovery-advisor.exe` instead of `recovery-advisor`. Requires Python 3.12+.

Provide your Garmin Personal API key using `GARMIN_PERSONAL_API_KEY` or the official client’s supported key file. For AI analysis, configure `OPENAI_API_KEY` in the same user account that runs the CLI or scheduled task. Do not put either key into `settings.json`.

### Upgrade from v2.6

1. Back up the existing `data/`, `settings.json` and `reports/` directories.
2. Extract v2.7 into a **new folder**. Copy those files/directories into the new folder; do not copy the old Python source or virtual environment.
3. Create the v2.7 virtual environment and run `pip install -e .` as shown above.
4. Run `recovery-advisor config`, `recovery-advisor status`, then `recovery-advisor review` to verify your preserved data and settings.
5. If you want to run scheduled syncs from the new folder, disable the old task and reinstall with the v2.7 script below. Do not let a task pointing to the old directory continue running accidentally.

The database format and synchronization checkpoints are unchanged. Existing athlete feedback, classification overrides and locally stored notes are retained. No data migration is needed beyond copying the existing local data.

## Automatic Garmin synchronization

After a successful manual `recovery-advisor sync`, install the Windows scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
Get-ScheduledTask -TaskName EnduranceAdvisor-GarminSync
```

The scheduler invokes `recovery-advisor sync` daily at **10 AM and 7 PM Windows local time**. The collector resumes from separate last-successful checkpoints for activities and individual wellness data types, allowing it to catch up after downtime. Use `manage backfill 30` only to search for older workouts uploaded *after* their date was already successfully synchronized.

To remove the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_schedule.ps1
```

The task typically runs while you are signed in, and Windows may run a missed task when the computer next becomes available. Set the PC timezone consistently with `timezone` in `settings.json` (normally `America/Chicago`).

## Why Garmin write-back is not included

In v2.5 we tested a direct, unofficial Garmin Connect web `PUT` call using a bearer token obtained from the Personal API key. Authentication succeeded, but the write operation returned **HTTP 404** and `{"error":"NOT_FOUND","status":404}`. The update could not be verified. This shows that the tested endpoint/authentication combination was not usable; it does **not** establish that every Garmin write capability is unavailable.

V2.6 removed Garmin write-back and v2.7 **keeps it removed**: no unofficial web-write dependency, extra login or `garmin-notes` commands. Garmin activity descriptions and wellness notes are still *read* for analysis. Feedback and AI interpretation remain in local SQLite and generated Markdown reports. Old unused write-back backup tables, if present, are preserved rather than deleted.

## Data model, privacy, and analysis limits

- 30-day rolling context, with the most recent 7 days used for a standard review.
- Training load and sport breakdowns are computed from stored activity data; feedback-only Garmin entries are excluded from workout totals when classified with sufficient confidence.
- Optional subjective questions are saved as athlete observations, with workout association when unambiguous.
- The 0–100 recovery score is an **experimental, unvalidated heuristic**. A period without reported symptoms is a weak favorable signal, not proof of recovery. Athlete-provided resting HR and sleep schedule are reference assumptions, not measured current status.
- Raw profile and GPS identifiers are filtered from locally stored workout summaries, but SQLite and reports may still contain sensitive health and symptom information. Keep private backups and do not distribute them.
- No diagnostic claims should be made from Garmin training data alone. New severe or concerning symptoms need appropriate medical attention.

Local files in an editable project installation: `settings.json`, `data/advisor.sqlite3`, and `reports/`. To use a different private data directory, set `RECOVERY_ADVISOR_HOME`. For a non-editable wheel installation, the default is `~/.recovery-advisor`.

## Project structure

```text
recovery_advisor/
  cli.py              # small, simplified CLI (user-facing)
  legacy_cli.py       # original v2.6 command handlers for compatibility
  garmin_sync.py      # official Garmin Personal API reads
  storage.py          # SQLite and privacy filtering
  classification.py   # workouts versus notes-only entries
  feedback.py         # local observations and workout links
  analysis.py         # training/recovery measurements
  reporting.py        # deterministic and optional AI reviews
  settings.py         # configuration and upgrades
  constants.py, paths.py
advisor.py            # small backward-compatible Python launcher
install_schedule.ps1  # Windows 10 AM / 7 PM schedule
```

## Offline tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
recovery-advisor --help
recovery-advisor manage --help
recovery-advisor --as-of 2026-11-08 manage import examples\sample_activities.json
recovery-advisor --as-of 2026-11-08 review
```

The tests do not contact Garmin or OpenAI. They verify the interface and existing logic but do not guarantee any Garmin endpoint remains available. The removed write endpoint is never called.
