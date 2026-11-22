# Recovery Advisor v2.3

An AI-powered endurance advisor that helps amateur athletes understand their body's response to training and make smarter decisions about recovery, nutrition, and future workouts.

**What's new in v2.3:** The former 1,407-line `advisor.py` is now a small compatibility launcher backed by a maintained `recovery_advisor` package. Installing the package provides a real `recovery-advisor` command. The Garmin sync and athlete-facing features of v2.2 are retained; there is **no SQLite schema change** in this release.

## Windows quick start

From PowerShell, extract the ZIP, open the `endurance_advisor_mvp_v2_3` folder, and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\Activate.ps1
recovery-advisor --help
recovery-advisor status
```

If PowerShell blocks `Activate.ps1`, you can allow it **for this PowerShell session only**:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

An activated virtual environment places `.venv\Scripts` on your session's `PATH`, so you can type `recovery-advisor` without a Python path. In a **new** PowerShell window, activate the environment again. You can alternatively run `.\.venv\Scripts\recovery-advisor.exe status` without activation. `pip install -e .` is an **editable installation**: source changes in the extracted folder take effect without reinstalling, except when changing entry points or package metadata.

Other platforms can install using `python -m venv .venv; .venv/bin/python -m pip install -e .; source .venv/bin/activate` (as separate commands) and then run `recovery-advisor`.

## Upgrade from v2.2 without losing your data

1. **Disable the existing `EnduranceAdvisor-GarminSync` Windows task** before changing folders; do not leave two scheduled copies active.
2. Back up your current `settings.json`, `data/`, and optionally `reports/`.
3. Extract v2.3 into a **new folder**. Copy the three backed-up items into that folder (do not overwrite the new source files).
4. Create a new virtual environment and install the project using the quick-start commands above.
5. Run `recovery-advisor status`, followed by a manual `recovery-advisor sync` after your API key is configured. Previous checkpoint dates and athlete feedback remain in SQLite.
6. Reinstall the Windows task by running `powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1` from the **v2.3 directory**. This task calls the installed CLI rather than `advisor.py`.

All configured settings, including 30-day baseline, 7-day analysis, manual resting-HR reference, body mass and race goal, remain in `settings.json`. The note-only classifier remains automatic; any rare corrections stay in SQLite, never `settings.json`.

**API key:** Follow Garmin Personal API's own key configuration instructions. The official client reads `GARMIN_PERSONAL_API_KEY` or a supported user-specific key file. Never place a Garmin or OpenAI key in `settings.json` or commit it to Git. For the Windows task, configure keys for the same signed-in Windows account that runs the task, not just in a transient PowerShell session. The program does not require the key for offline `status`, `report`, `feedback`, or `import-json` commands.

## Commands

Once the virtual environment is active:

```powershell
recovery-advisor sync
recovery-advisor status
recovery-advisor report
recovery-advisor ask
recovery-advisor feedback --question "Felt unusually fatigued after my long ride"
recovery-advisor activities --days 30
recovery-advisor review-activities --days 30
recovery-advisor classify 12345678 feedback_only
recovery-advisor classify 12345678 auto
recovery-advisor sync --backfill-days 30 --refresh-notes
recovery-advisor set resting_hr_bpm 50
recovery-advisor set weight_kg 70
recovery-advisor set ai_reports_enabled true
```

The `sync` command uses successful **per-endpoint checkpoint dates** and can catch up after several missed days. It avoids duplicate activities and fetches changed or missing details, including Garmin workout notes. `--backfill-days` is for the less-common case of a historical workout that Garmin received **after** a successful checkpoint. The `feedback` command stores a dated observation even without AI; `ask` stores the question first and then calls AI. By default, `report` is deterministic and makes no AI API call.

`recovery-advisor --as-of 2026-11-08 report` is available for offline historical checks; the `--as-of` option must appear **before** the subcommand.

### Recovery context score

The 0–100 recovery score is an **experimental heuristic**, not validated readiness or medical clearance. It uses observed training, limited available wellness indicators, and feedback. Under the default `issues_only` reporting convention, silence adds only a small favorable adjustment and remains a weak signal, not proof of full recovery. If any symptom warrants medical evaluation, a score should not override that concern.

## Modules and responsibilities

```text
endurance_advisor_mvp_v2_3/
├── pyproject.toml                 # packaging metadata and installed console command
├── advisor.py                     # small v2.2-compatible launcher/import facade
├── install_schedule.ps1           # Windows task: recovery-advisor sync, 10 AM + 7 PM
├── uninstall_schedule.ps1
├── settings.example.json
├── requirements.txt               # alternate pip install -r requirements.txt
├── recovery_advisor/
│   ├── __main__.py                # python -m recovery_advisor fallback
│   ├── cli.py                     # argument parsing and command dispatch
│   ├── settings.py                # settings, migration and user defaults
│   ├── paths.py                   # persistent paths independent of current directory
│   ├── constants.py               # configuration defaults and allowed data fields
│   ├── storage.py                 # SQLite, data sanitization and import
│   ├── garmin_sync.py             # checkpointed Garmin API collection
│   ├── classification.py          # automatic workouts-vs-notes classification
│   ├── analysis.py                # 7-day analysis, baseline and recovery score
│   ├── reporting.py               # deterministic report and optional LLM review
│   └── feedback.py                # athlete observations, AI Q&A and check-ins
├── tests/
│   ├── test_advisor.py            # original v2.2 regression tests
│   └── test_cli_package.py        # CLI/path/package regression tests
├── examples/
└── data/, reports/, settings.json # locally created, excluded from Git
```

Dependencies point **in one direction**: storage/configuration → Garmin/classification → analysis → reporting → feedback/CLI. The CLI is an adapter and should not contain domain calculations. The thin `advisor.py` facade remains for older commands and tests but new code should import from the package's dedicated modules.

## Windows Task Scheduler

After manually verifying `recovery-advisor sync`:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
Get-ScheduledTask -TaskName EnduranceAdvisor-GarminSync
```

The task invokes `.venv\Scripts\recovery-advisor.exe sync` at **10 AM and 7 PM Windows local time**, so activation is not required for scheduled runs. The task is created for the signed-in Windows user. Configure your Windows time zone consistently with the `timezone` value in `settings.json` (default: `America/Chicago`). The task is not guaranteed to run while the PC is powered off or the account is signed out. To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_schedule.ps1
```

## Optional AI reports

Set `OPENAI_API_KEY` in the environment available to the CLI or scheduled-task user. Then:

```powershell
recovery-advisor set ai_reports_enabled true
recovery-advisor report
```

AI reports are disabled by default. Calls to OpenAI are billed separately and include minimally necessary training, nutrition and symptom context; no raw GPS, account identifiers, Garmin keys, or raw request diagnostics are sent. However, the context **does include potentially sensitive workout and health information**. Do not turn on AI unless you are comfortable with that data transfer. The app uses `store=False` when making its API request. A question about concerning symptoms is not a medical diagnosis; persistent or severe symptoms require appropriate professional assessment.

## Where files are stored

When run from an editable installation (`pip install -e .`) in this extracted folder, the application stores state in the **project folder** (regardless of where the command is launched):

- `settings.json`: personal settings
- `data/advisor.sqlite3`: workouts, wellness, checkpoints, notes and feedback
- `reports/`: Markdown reports and minimized JSON context

The optional environment variable `RECOVERY_ADVISOR_HOME` overrides the storage folder, for example to isolate a development run. When installed as a non-editable wheel without a project folder, the default storage folder is `~/.recovery-advisor`. Keep copies of `settings.json` and `data/` together when migrating. Both the SQLite database and report context may contain sensitive health information; they remain local and are excluded by `.gitignore`.

## Offline verification

Run the tests without Garmin credentials:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
recovery-advisor --help
recovery-advisor --as-of 2026-11-08 import-json examples\sample_activities.json
recovery-advisor --as-of 2026-11-08 report
```

For CLI testing in an empty directory without touching your actual data, set `RECOVERY_ADVISOR_HOME` to a temporary folder before running a command. Automated tests cover cached workouts, note classification, symptom persistence, checkpoint catch-up, and the installed command's offline behavior. **Live Garmin calls, OpenAI calls, and scheduled-task execution need validation on your computer.**
