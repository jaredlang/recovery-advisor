"""Small, task-oriented command line frontend.

Every prior v2.6 command still delegates to legacy_cli.main so existing user
scripts keep working; these commands are intentionally absent from top-level
help. New commands are thin argument translations, not duplicate business logic.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from . import legacy_cli
from .settings import load_config

MAIN_HELP = f"""recovery-advisor {__version__} — Garmin-based endurance recovery advisor

Common commands:
  recovery-advisor sync                         Fetch Garmin updates; refresh reports
  recovery-advisor review                       Generate the latest recovery report
  recovery-advisor ask [\"your question\"]      Ask AI; automatically save feedback
  recovery-advisor status                       Show sync and data status

Configuration and maintenance:
  recovery-advisor config                       Show personal settings
  recovery-advisor config weight 70             Set weight (kg)
  recovery-advisor config hr 50                 Set resting HR reference (bpm)
  recovery-advisor config ai on                 Enable optional AI reports
  recovery-advisor manage                       List occasional maintenance actions

Use 'recovery-advisor <command> --help' for options.
Existing v2.6 commands remain available for backward compatibility.
"""

MANAGE_HELP = """Occasional maintenance actions:
  recovery-advisor manage workouts [--days 30]       List workout records
  recovery-advisor manage uncertain [--days 30]      Review ambiguous activities
  recovery-advisor manage classify ID LABEL          Set workout|feedback_only|auto
  recovery-advisor manage feedback [--days 30]       List athlete feedback
  recovery-advisor manage note [\"observation\"]      Record feedback without AI
  recovery-advisor manage link FEEDBACK_ID ACTIVITY_ID
  recovery-advisor manage unlink FEEDBACK_ID
  recovery-advisor manage checkin                    Optional daily check-in
  recovery-advisor manage backfill 30                Recheck older Garmin workouts
  recovery-advisor manage backfill 30 --refresh-notes
  recovery-advisor manage import path/to/file.json   Import Garmin JSON offline

These actions affect only local data except 'backfill', which reads Garmin.
"""

ALIASES = {
    'goal': 'race_goal', 'race': 'race_goal', 'weight': 'weight_kg',
    'hr': 'resting_hr_bpm', 'rest-hr': 'resting_hr_bpm',
    'bedtime': 'usual_bedtime', 'wake': 'usual_wake_time',
    'diet': 'dietary_preferences', 'ai': 'ai_reports_enabled',
    'model': 'openai_model', 'baseline': 'baseline_days',
    'window': 'analysis_days', 'feedback-mode': 'feedback_reporting_mode',
}

LEGACY_COMMANDS = {
    'sync', 'report', 'ask', 'status', 'import-json', 'feedback',
    'feedback-list', 'link-feedback', 'unlink-feedback', 'checkin',
    'activities', 'review-activities', 'classify', 'mark-feedback',
    'unmark-feedback', 'set',
}


def _question_command(command: str, argv: list[str], prefix: list[str]) -> int:
    """Accept a natural-language positional question but retain old flags."""
    parser = argparse.ArgumentParser(prog=f'recovery-advisor {command}')
    parser.add_argument('message', nargs='*', help='Text to ask or record; prompted if omitted')
    parser.add_argument('--question', help='Backward-compatible explicit question')
    parser.add_argument('--event-day', help='YYYY-MM-DD when the experience occurred')
    parser.add_argument('--activity-id', help='Optional Garmin workout association')
    args = parser.parse_args(argv)
    if args.question is not None and args.message:
        parser.error('Use either quoted message text or --question, not both')
    question = args.question if args.question is not None else (' '.join(args.message) if args.message else None)
    converted = prefix + [command]
    if question is not None:
        converted += ['--question', question]
    if args.event_day:
        converted += ['--event-day', args.event_day]
    if args.activity_id:
        converted += ['--activity-id', args.activity_id]
    return legacy_cli.main(converted)


def _config(argv: list[str], prefix: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='recovery-advisor config',
        description='Show or update personal settings (stored locally in settings.json)',
        epilog='Examples: config weight 70; config hr 50; config goal "Ironman 70.3"; config ai on',
    )
    parser.add_argument('field', nargs='?', help='weight, hr, goal, bedtime, wake, diet, ai, model, baseline, window')
    parser.add_argument('value', nargs='?', help='New setting value')
    args = parser.parse_args(argv)
    if args.field is None or args.field == 'show':
        if args.value is not None:
            parser.error('Use config without arguments to view settings')
        conf = load_config()
        print('Personal settings:')
        for friendly, key in [
            ('Goal', 'race_goal'), ('Weight (kg)', 'weight_kg'),
            ('Resting HR reference (bpm)', 'resting_hr_bpm'),
            ('Usual bedtime', 'usual_bedtime'), ('Usual wake time', 'usual_wake_time'),
            ('Diet', 'dietary_preferences'), ('AI reports enabled', 'ai_reports_enabled'),
            ('AI model', 'openai_model'), ('Baseline days', 'baseline_days'),
            ('Analysis days', 'analysis_days'), ('Feedback mode', 'feedback_reporting_mode'),
        ]:
            print(f'  {friendly}: {conf.get(key) if conf.get(key) is not None else "not set"}')
        print('Change one with: recovery-advisor config weight 70')
        return 0
    key = ALIASES.get(args.field, args.field)
    if key not in set(ALIASES.values()):
        parser.error(f'Unknown setting: {args.field}. Use config --help for examples')
    if args.value is None:
        parser.error(f'Provide a value for {args.field}')
    value = args.value
    if key == 'ai_reports_enabled':
        switches = {'on': 'true', 'off': 'false', 'yes': 'true', 'no': 'false'}
        value = switches.get(value.lower(), value.lower())
    return legacy_cli.main(prefix + ['set', key, value])


def _manage(argv: list[str], prefix: list[str]) -> int:
    if not argv or argv[0] in ('-h', '--help'):
        print(MANAGE_HELP)
        return 0
    cmd, *rest = argv
    direct = {
        'workouts': 'activities', 'uncertain': 'review-activities',
        'classify': 'classify', 'notes': 'feedback-list', 'feedback': 'feedback-list',
        'link': 'link-feedback', 'unlink': 'unlink-feedback',
        'checkin': 'checkin', 'import': 'import-json',
    }
    if cmd == 'note':
        return _question_command('feedback', rest, prefix)
    if cmd == 'backfill':
        parser = argparse.ArgumentParser(prog='recovery-advisor manage backfill')
        parser.add_argument('days', type=int, help='Number of days to recheck (1–365)')
        parser.add_argument('--refresh-notes', action='store_true', help='Refetch activity descriptions')
        args = parser.parse_args(rest)
        translated = prefix + ['sync', '--backfill-days', str(args.days)]
        if args.refresh_notes:
            translated.append('--refresh-notes')
        return legacy_cli.main(translated)
    if cmd not in direct:
        print(f'Unknown maintenance command: {cmd}\n\n{MANAGE_HELP}', file=sys.stderr)
        return 2
    return legacy_cli.main(prefix + [direct[cmd]] + rest)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (['-h'], ['--help'], ['help']):
        print(MAIN_HELP)
        return 0
    if args == ['--version']:
        print(f'recovery-advisor {__version__}')
        return 0
    # Legacy entry point expects --as-of to precede the subcommand.
    prefix: list[str] = []
    if args[0] == '--as-of':
        if len(args) < 3:
            print('Expected --as-of YYYY-MM-DD followed by a command', file=sys.stderr)
            return 2
        prefix, args = args[:2], args[2:]
    cmd, *rest = args
    if cmd == 'review':
        return legacy_cli.main(prefix + ['report'] + rest)
    if cmd == 'ask':
        return _question_command('ask', rest, prefix)
    if cmd == 'config':
        return _config(rest, prefix)
    if cmd == 'manage':
        return _manage(rest, prefix)
    if cmd in LEGACY_COMMANDS:
        return legacy_cli.main(prefix + args)
    print(f'Unknown command: {cmd}\n\n{MAIN_HELP}', file=sys.stderr)
    return 2


def run() -> None:
    """Installed console script with the same friendly error handling as v2.6."""
    import sqlite3
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, ImportError, sqlite3.DatabaseError) as exc:
        print('Error:', str(exc), file=sys.stderr)
        sys.exit(1)
