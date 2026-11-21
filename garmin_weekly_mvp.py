"""Garmin Connect Personal API: one-week data audit and mock coaching review.

Install: py -m pip install requests
Run:     py garmin_weekly_mvp.py
Offline: py garmin_weekly_mvp.py --from-file garmin_activities.json
Dates:   py garmin_weekly_mvp.py --start 2026-11-02 --end 2026-11-08

By default, fetches the last COMPLETE Monday-Sunday week, not the last 7 rolling days.
The output intentionally omits common identity and GPS fields, but treat it as
private health information and inspect it before sharing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import requests

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ACTIVITIES_PATH = "/activitylist-service/activities/search/activities"
PAGE_SIZE = 50

# Remove identity/location from the local report intended for sharing.
# Not a formal de-identification guarantee: review the file before sharing.
PRIVATE_EXACT = {
    "ownerid", "ownerdisplayname", "ownerfullname", "ownerprofileimageurlsmall",
    "ownerprofileimageurlmedium", "ownerprofileimageurllarge",
    "userfullname", "userdisplayname", "firstname", "lastname", "email",
    "activityuuid", "userid", "useridstring", "profileimageurl", "username",
    "deviceid", "serialnumber", "address", "privacy", "locationname",
}
PRIVATE_FRAGMENTS = (
    "latitude", "longitude", "coordinate", "profileimage", "gps", "postaladdress",
)


def sanitized(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    if isinstance(value, dict):
        return {
            k: sanitized(v)
            for k, v in value.items()
            if k.lower() not in PRIVATE_EXACT
            and not any(s in k.lower() for s in PRIVATE_FRAGMENTS)
        }
    return value


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Dates must be YYYY-MM-DD") from exc


def complete_week() -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    current_monday = today - dt.timedelta(days=today.weekday())
    return current_monday - dt.timedelta(days=7), current_monday - dt.timedelta(days=1)


def as_local_date(a: dict) -> dt.date | None:
    stamp = a.get("startTimeLocal") or a.get("startTimeGMT")
    try:
        return dt.date.fromisoformat(str(stamp)[:10])
    except (TypeError, ValueError):
        return None


def secs(a: dict, key: str = "duration") -> float:
    try:
        return float(a.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def api_get(session: requests.Session, path: str, params: dict | None = None) -> tuple[Any, dict]:
    try:
        r = session.get(BASE + path, params=params, timeout=30)
        if r.status_code >= 400:
            return None, {"status": "http_error", "http_status": r.status_code}
        data = r.json() if r.content else None
        status = "ok" if data not in (None, [], {}) else "empty"
        return data, {"status": status, "http_status": r.status_code}
    except requests.RequestException as exc:
        return None, {"status": "network_error", "error": type(exc).__name__}
    except ValueError:
        return None, {"status": "invalid_json"}


def connect(key: str) -> requests.Session:
    r = requests.post(
        BASE + TOKEN_PATH,
        headers={"Authorization": f"ApiKey {key}"},
        timeout=30,
    )
    r.raise_for_status()
    # Current Garmin Personal API sends a JWT as a plain-text response.
    token = r.text.strip().strip('"')
    if not token:
        raise RuntimeError("Garmin returned an empty access token")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def fetch_week(start: dt.date, end: dt.date, with_food_logs: bool) -> dict:
    key = (os.getenv("GARMIN_CONNECT_API_KEY") or getpass.getpass(
        "Garmin Personal API key (hidden): "
    )).strip()
    if not key:
        raise RuntimeError("No API key provided")
    print("Authenticating with Garmin...")
    session = connect(key)
    api_status = {}
    activities: list[dict] = []
    seen: set[str] = set()
    try:
        print(f"Fetching all activities for {start} through {end}...")
        offset = 0
        for _ in range(100):
            batch, status = api_get(session, ACTIVITIES_PATH, {
                "start": offset,
                "limit": PAGE_SIZE,
                "startDate": str(start),
                # Allow for inclusive/exclusive end-date differences;
                # filter again using the local date below.
                "endDate": str(end + dt.timedelta(days=1)),
            })
            if status["status"] not in ("ok", "empty"):
                raise RuntimeError(f"Activity search failed: {status}")
            if not isinstance(batch, list):
                raise RuntimeError("Unexpected activities response (not a list)")
            for a in batch:
                if not isinstance(a, dict):
                    continue
                day = as_local_date(a)
                if day is None or not (start <= day <= end):
                    continue
                aid = str(a.get("activityId") or "")
                if aid and aid not in seen:
                    seen.add(aid)
                    activities.append(sanitized(a))
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        else:
            raise RuntimeError("Too many activity pages; stopping after 100 pages")
        api_status["activities"] = {"status": "ok", "records": len(activities)}
        activities.sort(key=lambda a: a.get("startTimeLocal", ""))
        print(f"Found {len(activities)} workouts. Fetching activity details...")
        details: dict[str, Any] = {}
        detail_checks: dict[str, Any] = {}
        for a in activities:
            aid = a.get("activityId")
            if not aid:
                continue
            payload, status = api_get(session, f"/activity-service/activity/{aid}")
            details[str(aid)] = sanitized(payload)
            detail_checks[str(aid)] = status
        api_status["activity_details"] = detail_checks

        tasks = {
            "daily_summaries": (
                "/usersummary-service/v2/dailysummaries",
                {"startDate": str(start), "endDate": str(end)},
            ),
            "sleep_summaries": (
                "/sleep-service/sleep/dailySleepSummariesByDate",
                {"startDate": str(start), "endDate": str(end)},
            ),
            "hrv_summaries": (
                f"/hrv-service/hrv/daily/{start}/{end}", None,
            ),
            "body_battery": (
                "/wellness-service/wellness/bodyBattery/reports/daily",
                {"startDate": str(start), "endDate": str(end)},
            ),
            "hydration": (
                f"/usersummary-service/stats/hydration/daily/{start}/{end}", None,
            ),
            "weight": (
                f"/weight-service/weight/range/{start}/{end}",
                {"includeAll": "true"},
            ),
            "heart_rate_zones": ("/biometric-service/heartRateZones/v2", None),
        }
        wellness = {}
        for name, (path, params) in tasks.items():
            print(f"Checking {name}...")
            data, status = api_get(session, path, params)
            wellness[name] = sanitized(data)
            api_status[name] = status

        if with_food_logs:
            food_logs = {}
            for d in (start + dt.timedelta(days=i) for i in range((end - start).days + 1)):
                data, status = api_get(session, f"/nutrition-service/food/logs/{d}")
                food_logs[str(d)] = {"data": sanitized(data), "api_status": status}
            wellness["food_logs"] = food_logs
            api_status["food_logs"] = {"status": "checked", "days": len(food_logs)}

        return {
            "period": {"start": str(start), "end": str(end)},
            "source": "garmin_personal_api",
            "notes": ["Data may contain sensitive fitness information. Review before sharing.",
                      "API endpoint presence does not guarantee that a device/user records the metric."],
            "activities": activities,
            "activity_details": details,
            "wellness": wellness,
            "api_status": api_status,
        }
    finally:
        session.close()


def offline_bundle(path: Path, start: dt.date, end: dt.date) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        a = data
    elif isinstance(data, dict) and isinstance(data.get("activities"), list):
        a = data["activities"]
    else:
        raise RuntimeError("Offline JSON must be an activity list or a weekly bundle")
    selection = [sanitized(x) for x in a
                 if isinstance(x, dict) and as_local_date(x) is not None
                 and start <= as_local_date(x) <= end]
    selection.sort(key=lambda x: x.get("startTimeLocal", ""))
    return {
        "period": {"start": str(start), "end": str(end)},
        "source": "uploaded_activity_snapshot_not_complete_week",
        "notes": ["Offline activity snapshot may omit workouts from the requested week.",
                  "Sleep, HRV, recovery and nutrition data not available in this snapshot."],
        "activities": selection,
        "activity_details": {},
        "wellness": {},
        "api_status": {"activities": {"status": "offline_partial", "records": len(selection)}},
    }


def present(a: dict, key: str) -> bool:
    return a.get(key) is not None


def metric_coverage(activities: list[dict]) -> dict[str, dict]:
    fields = ["distance", "duration", "averageHR", "maxHR", "calories",
              "averageRunningCadenceInStepsPerMinute", "avgPower", "normPower",
              "trainingStressScore", "intensityFactor", "hrTimeInZone_1",
              "powerTimeInZone_1"]
    return {name: {"present": sum(present(a, name) for a in activities),
                   "total": len(activities)} for name in fields}


def fmt_duration(s: float) -> str:
    return f"{int(round(s / 60))} min"


def build_review(bundle: dict) -> str:
    activities = bundle["activities"]
    start, end = bundle["period"].values()
    run = [a for a in activities if "run" in (a.get("activityType") or {}).get("typeKey", "")]
    cycling = [a for a in activities if any(t in (a.get("activityType") or {}).get("typeKey", "")
                                               for t in ("cycling", "ride", "bike"))]
    total = sum(secs(a) for a in activities)
    cal = sum(float(a.get("calories") or 0) for a in activities)
    lines = [
        f"# Prototype Training & Recovery Review | {start} to {end}", "",
        "**Status:** Prototype analysis; not a diagnosis, personalized training plan, or complete readiness assessment.",
        "", "## Recorded workload", "",
        f"- {len(activities)} activities, {fmt_duration(total)} recorded exercise time, "
        f"{cal:,.0f} Garmin-estimated activity kcal.",
        f"- Running: {len(run)} sessions, {sum(secs(a, 'distance') for a in run)/1000:.2f} km.",
        f"- Cycling: {len(cycling)} sessions, {sum(secs(a, 'distance') for a in cycling)/1000:.2f} km.",
    ]
    daily = Counter(str(as_local_date(a)) for a in activities)
    busy = max(daily, key=daily.get) if daily else None
    if busy:
        day_sessions = [a for a in activities if str(as_local_date(a)) == busy]
        lines += [f"- Busiest recorded day: {busy}, {len(day_sessions)} sessions "
                  f"({fmt_duration(sum(secs(a) for a in day_sessions))})."]
    lines += ["", "## Coaching observations", ""]
    if activities:
        lines.append("- Look at how sessions are clustered, not just weekly distance. "
                     "Consecutive sessions can be intentional, but recovery and effort context matter.")
        if busy and len([a for a in activities if str(as_local_date(a)) == busy]) > 1:
            lines.append(f"- {busy} contained multiple workouts. Check whether they were meant "
                         "to form a brick or other combined endurance session.")
        lines.append("- Do not infer improving fitness, overtraining, or race readiness from a single "
                     "week without comparable history and a stated race goal.")
        if cycling and any(a.get("intensityFactor") is not None for a in cycling):
            lines.append("- Cycling intensity-factor and power-zone settings should be validated "
                         "before interpreting high-intensity minutes.")
    else:
        lines.append("- No activities were returned for the selected period. Check syncing and date filters.")
    lines += ["", "## Recovery nutrition (example guidance, not a personal prescription)", "",
              "- After longer or clustered endurance sessions, include a substantial carbohydrate "
              "source plus protein in the next meal (for example rice and eggs, or a sandwich "
              "with yogurt and fruit).",
              "- A common post-exercise protein serving is about 20–40 g for adults; exact "
              "needs depend on body weight, dietary pattern, and total daily intake.",
              "- For rapid glycogen restoration before another session within a few hours, "
              "carbohydrate timing and amount matter more; the plan needs body mass and "
              "time-until-next-workout.",
              "- Replace fluids according to thirst, environmental conditions and measured "
              "sweat losses rather than using Garmin calorie estimates as a hydration target.",
              "", "## Data-quality / MVP validation", "",
              f"- Data source: `{bundle.get('source')}`.",
              f"- Activity field coverage: `{json.dumps(metric_coverage(activities), separators=(',', ':'))}`."]
    for key, status in bundle.get("api_status", {}).items():
        if key == "activity_details" and isinstance(status, dict):
            ok = sum(v.get("status") in ("ok", "empty") for v in status.values())
            lines.append(f"- activity_details: {ok}/{len(status)} endpoints reachable/returned")
        else:
            lines.append(f"- {key}: {status.get('status', 'unknown') if isinstance(status, dict) else status}")
    lines += ["", "## Questions for athlete onboarding", "",
              "1. What event, date, distance, and result are you training for?",
              "2. Which workouts were intended as easy, tempo, intervals, long endurance, or brick?",
              "3. What was your session RPE and next-morning soreness / energy?",
              "4. What did you eat/drink during and after the largest sessions?",
              "", "**Data limitations:** An activity snapshot cannot establish a full week's "
              "completeness; wearable recovery data may be absent. Missing data is not evidence "
              "of poor recovery. The review is a rule-based prototype, not an AI coaching model."]
    return "\n".join(lines) + "\n"


def main() -> int:
    default_start, default_end = complete_week()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=parse_date, help="Week start YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, help="Week end YYYY-MM-DD")
    parser.add_argument("--from-file", type=Path,
                        help="Use a preexisting activity JSON instead of calling Garmin")
    parser.add_argument("--with-food-logs", action="store_true",
                        help="Check each day's food-log endpoint (adds 7 requests)")
    parser.add_argument("--outdir", type=Path, default=Path("."),
                        help="Folder for JSON and Markdown files")
    args = parser.parse_args()
    start = args.start or default_start
    end = args.end or default_end
    if start > end or (end - start).days > 30:
        parser.error("Select an ordered date range of at most 31 days")
    if (args.start is None) != (args.end is None):
        parser.error("Pass both --start and --end or neither")
    try:
        bundle = (offline_bundle(args.from_file, start, end)
                  if args.from_file else fetch_week(start, end, args.with_food_logs))
        args.outdir.mkdir(parents=True, exist_ok=True)
        base = f"garmin_week_{start}_to_{end}"
        jsonpath = args.outdir / (base + ".json")
        mdpath = args.outdir / (base + "_review.md")
        jsonpath.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
        mdpath.write_text(build_review(bundle), encoding="utf-8")
        print(f"\nSaved {jsonpath.resolve()}")
        print(f"Saved {mdpath.resolve()}")
        print("The JSON has common identity/location fields removed; review it before sharing.")
        return 0
    except (RuntimeError, requests.RequestException, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
