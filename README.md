# Endurance Advisor MVP

Local-first prototype for **amateur endurance athletes**: automatically retrieve Garmin workouts and available health summaries, interpret a **rolling seven-day window** against a **14-day local context**, collect brief subjective recovery check-ins, and produce recovery/nutrition guidance. No medical diagnosis. **Python 3.12+**, designed for Windows.

## What is implemented

- Garmin's **official** `garmin-connect-personal-api-python` library: one cached OAuth bearer token, activity search (paginated), activity details for new/changed entries or previously missing details, 4 date-range recovery summary endpoints.
- First sync: 14 inclusive calendar days ending today. Subsequent syncs: **today + yesterday** (two days) to catch late uploads; adjust `daily_sync_lookback_days` in `settings.json` if needed. Old records stay in SQLite.
- No duplicate workout rows: keyed by Garmin activity ID; summary changes trigger an update and refetch of details. Repeating an identical import does not duplicate rows.
- SQLite database `data/advisor.sqlite3`; personally identifying/GPS fields are not copied from Garmin activity summaries. For wellness, only selected scalar fields are stored (no raw per-minute series).
- Deterministic report every run at `reports/YYYY-MM-DD_recovery.md`. A minimized JSON analysis context is written to `reports/YYYY-MM-DD_context.json`.
- **Optional** AI report using the OpenAI API when explicitly enabled. Unchanged context avoids redundant scheduled AI requests; API requests use `store=False`. `reports/YYYY-MM-DD_ai_review.md` contains its interpretation. Users can also ask questions at any time with `ask`, without a new Garmin request.
- `checkin` for daily fatigue, soreness, energy, symptoms and meal observations.
- `install_schedule.ps1`: Windows Task Scheduler at **10:00 AM and 7:00 PM** local system time, with StartWhenAvailable (while signed in).
- API failures are recorded. If Garmin rate limits an endpoint (429), the code does not continue issuing recovery endpoint requests; the local report remains available and shows sync status.

**Limitations:** The code and offline tests are verified locally, but a live authenticated run against your Garmin account is required to verify the actual sleep/HRV/Body Battery response shapes and which metrics your device provides. Date-range endpoints may return data in formats not yet supported by the conservative parser; unknown shapes are skipped, not invented. A 14-day baseline is provisional. Scheduling on your PC is not possible from within ChatGPT; you must run the setup script. Windows tasks are configured for the signed-in user, not an always-on server. OpenAI API usage is billed separately and the program sends the *minimized analysis context and your check-in notes*, which may contain sensitive health information, to OpenAI when AI features are enabled.

## Install in Windows PowerShell

1. Extract the entire ZIP into a folder, e.g. `C:\Users\YOU\endurance_advisor_mvp`.
2. Install Python 3.12 or later, if needed. From PowerShell **in the extracted folder**:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. Place your existing Garmin **Personal API key** in the default per-user file used by Garmin's official client. This example prompts without echoing the value and avoids putting it in shell history:

```powershell
New-Item -ItemType Directory -Force "$HOME\.config\garmin" | Out-Null
$secret = Read-Host "Garmin Personal API key" -AsSecureString
$plaintext = [System.Net.NetworkCredential]::new('', $secret).Password
[System.IO.File]::WriteAllText("$HOME\.config\garmin\connect-personal-api-key", $plaintext)
Remove-Variable plaintext, secret
```

Alternatively, use the `GARMIN_PERSONAL_API_KEY` environment variable. Never paste a key into a chat, `.py` file, command-line argument, screenshot, or Git repository. Keep the token/key file readable only by your account.

4. Run the live **initial** synchronization and read its results:

```powershell
.\.venv\Scripts\python.exe advisor.py sync
.\.venv\Scripts\python.exe advisor.py status
$day=(Get-Date).ToString("yyyy-MM-dd")
notepad ".\reports\${day}_recovery.md"
```

If your PC's locale is not America/Chicago, edit `timezone` in `settings.json` (initially generated automatically) and make sure the **Windows clock time zone** matches the desired schedule; Task Scheduler uses the local OS clock.

## Optional: AI-generated reports

Without an OpenAI key you get a deterministic, useful but limited weekly report. AI analysis is **opt-in**. The model will receive a minimized representation of the seven-day activities, 14-day recovery context and any subjective check-ins; not the Garmin token or GPS/profile fields. Your notes might nonetheless contain sensitive information, and OpenAI API charges apply.

Set `OPENAI_API_KEY` in the account used for scheduling (example, prompts without echoing the key and stores it in Windows user environment settings):

```powershell
$secret = Read-Host "OpenAI API key" -AsSecureString
$plaintext = [System.Net.NetworkCredential]::new('', $secret).Password
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $plaintext, 'User')
$env:OPENAI_API_KEY = $plaintext  # Also make it available in this PowerShell session
Remove-Variable plaintext, secret
.\.venv\Scripts\python.exe advisor.py set ai_reports_enabled true
.\.venv\Scripts\python.exe advisor.py report
```

Existing shells or already-running sessions may need to be reopened to see a new user environment variable. If AI calls fail, the deterministic report remains available. Model can be changed using `advisor.py set openai_model MODEL_NAME`.

## Daily usage

```powershell
.\.venv\Scripts\python.exe advisor.py checkin     # add subjective recovery observations
.\.venv\Scripts\python.exe advisor.py ask         # ask a new question using cached data; OpenAI required
.\.venv\Scripts\python.exe advisor.py report      # regenerate report; no Garmin calls
.\.venv\Scripts\python.exe advisor.py status      # last run, coverage, endpoint status
.\.venv\Scripts\python.exe advisor.py set race_goal "Half-distance triathlon in spring"
.\.venv\Scripts\python.exe advisor.py set weight_kg 70.3
.\.venv\Scripts\python.exe advisor.py set dietary_preferences "No dairy"
```

If an API endpoint fails or Garmin has not synced your watch, the report says so; it does not infer that no workout occurred. Check `status`. The calendar date for the report is the local date in your configured timezone. `--as-of` permits reproducible offline reports, e.g. `advisor.py --as-of 2026-11-08 report`.

## Enable the automatic 10 AM / 7 PM schedule

After confirming manual sync works, from PowerShell in the extracted folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
Get-ScheduledTask -TaskName EnduranceAdvisor-GarminSync
```

The script installs ONE task with **two daily triggers**. The task runs `advisor.py sync` in the virtual environment and uses cached credentials under your user account. It runs only while that account is signed in. `StartWhenAvailable` can pick up missed runs after the computer becomes available, but there is no guarantee of execution when it is powered off or unavailable. Do not move the extracted folder without reinstalling the task. To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_schedule.ps1
```

## Offline testing (no Garmin/OpenAI network calls)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe advisor.py --as-of 2026-11-08 import-json examples\sample_activities.json
.\.venv\Scripts\python.exe advisor.py --as-of 2026-11-08 report
```

Importing a file **does not** mark live Garmin initialization complete. Next live `sync` still fetches its initial 14 days. Re-import is idempotent. If you import your prior `garmin_activities.json` instead, it is stored in a private local SQLite database; the program only retains allowlisted activity fields.

## Data, privacy and controls

- Local files: `data/` (SQLite), `reports/` (text/JSON), `settings.json` (configuration). All excluded from Git by `.gitignore`.
- The minimized context includes workout metrics and subjective notes, but excludes GPS coordinates, names, profile images and activity IDs. Never assume it is anonymous; contextual health data can still be sensitive.
- `report` and `ask` never call Garmin. `sync` is the only command that calls Garmin.
- AI reports are **disabled by default**. If disabled, no data is sent to OpenAI; any previously generated AI reports remain on disk until you delete them.
- To remove your local records, close the program and delete `data/`, `reports/`, `settings.json`; this doesn't revoke Garmin credentials or access tokens. Revoke your Personal API key in Garmin Connect if needed.
- Treat potential medical symptoms as observations, not diagnoses. Seek professional care for persistent/worsening symptoms; chest pain, fainting or severe breathing problems require urgent evaluation.

## API references

Official Garmin Personal API client: https://github.com/garmin/connect-personal-api-python

Garmin Personal API FAQ: https://developer.garmin.com/gc-developer-program/personal/personal-faq/

Official API endpoint documentation: https://developer.garmin.com/gc-developer-program/personal/swagger-docs/

## Implementation structure

- `advisor.py` – command-line app, SQLite schema, incremental sync, deterministic+optional AI report, Q&A and check-ins.
- `install_schedule.ps1`, `uninstall_schedule.ps1` – local Windows Task Scheduler controls.
- `tests/test_advisor.py` – entirely offline behavior tests with a fake Garmin client.
- `examples/sample_activities.json` – synthetic activity data, not your private Garmin log.
