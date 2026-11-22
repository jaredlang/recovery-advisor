"""User settings, defaults and backward-compatible migrations."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .paths import CONFIG_PATH, DB
from .constants import DEFAULT_CONFIG
from .storage import sql_open
from .classification import set_classification_override

def now_local(config: dict) -> dt.datetime:
    return dt.datetime.now(ZoneInfo(config['timezone']))

def load_config(config_path: Path | None = None, db_path: Path | None = None) -> dict:
    config_path = config_path or CONFIG_PATH
    db_path = db_path or DB
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + '\n', encoding='utf-8')
    settings = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(settings, dict):
        raise ValueError('settings.json must contain a JSON object')
    # Migration: preserve established v2.1 manual classifications in SQLite,
    # then permanently remove the deprecated ID list from settings.json.
    legacy_ids = settings.pop('notes_only_activity_ids', None)
    if legacy_ids is not None:
        if not isinstance(legacy_ids, list):
            raise ValueError('Legacy notes_only_activity_ids must be a list')
        with contextlib.closing(sql_open(db_path)) as db:
            for activity_id in legacy_ids:
                if str(activity_id).isdecimal():
                    set_classification_override(db, str(activity_id), 'feedback_only',
                                                origin='v2_1_migration')
            db.commit()
    was_legacy = 'daily_sync_lookback_days' in settings
    settings.pop('daily_sync_lookback_days', None)
    if was_legacy and settings.get('baseline_days') == 14:
        settings['baseline_days'] = 30
    merged = {**DEFAULT_CONFIG, **settings}
    if settings != merged or legacy_ids is not None:
        save_config(merged, config_path=config_path)
    return merged

def save_config(settings: dict, config_path: Path | None = None) -> None:
    config_path = config_path or CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')

