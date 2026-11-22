"""Persistent project locations, independent of launch working directory."""
from pathlib import Path
import os


def _root() -> Path:
    explicit = os.getenv('RECOVERY_ADVISOR_HOME')
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / 'settings.example.json').is_file():
        return candidate  # editable/source installation: preserve v2.2 layout
    return Path.home() / '.recovery-advisor'  # non-editable wheel installation


ROOT = _root()
DATA = ROOT / 'data'
DB = DATA / 'advisor.sqlite3'
REPORTS = ROOT / 'reports'
CONFIG_PATH = ROOT / 'settings.json'
