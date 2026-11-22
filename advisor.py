#!/usr/bin/env python3
"""Legacy launcher. Prefer `recovery-advisor` after `pip install -e .`."""
from __future__ import annotations

from recovery_advisor.paths import ROOT, DATA, DB, REPORTS, CONFIG_PATH
from recovery_advisor.constants import DEFAULT_CONFIG, ACTIVITY_KEYS, METRIC_FIELDS
from recovery_advisor.storage import (iso_now, sha, sql_open, to_float, activity_date, clean_activity,
    clean_detail, insert_activity, upsert_detail, nested_first_date, unpack_days,
    insert_wellness, insert_garmin_notes, state_get, state_set, status_put, import_activity_file)
from recovery_advisor.settings import now_local, save_config
from recovery_advisor.classification import (sport, NOTE_TITLE, SYMPTOM, SYMPTOM_CONTEXT,
    NON_EXERCISE_TYPES, TELEMETRY_KEYS, classification_overrides,
    set_classification_override, classify_activity, activity_classifications, query_range)
from recovery_advisor.garmin_sync import (error_label, fetch_activity_pages,
    endpoint_start, advance_checkpoint, sync_with_client, sync_online)
from recovery_advisor.analysis import (summarize_activities, baseline_references,
    context_data, estimate_recovery)
from recovery_advisor.reporting import (AI_POLICY, format_minutes, safe_markdown,
    deterministic_report, ai_response, ai_context_digest)
from recovery_advisor.feedback import (record_feedback, ask, read_score, checkin)
from recovery_advisor.cli import main, run

# For integrations that monkeypatch these two legacy path variables, preserve
# the historical behavior without mutating package-level process globals.
def load_config() -> dict:
    from recovery_advisor.settings import load_config as _load
    return _load(config_path=CONFIG_PATH, db_path=DB)


def write_report(db, today, config, with_ai=False):
    from recovery_advisor.reporting import write_report as _write
    return _write(db, today, config, with_ai=with_ai, reports_dir=REPORTS)


if __name__ == '__main__':
    run()
