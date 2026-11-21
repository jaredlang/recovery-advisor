"""Verify a Garmin Connect Personal API key and list recent workouts.

Install: python -m pip install requests
Run:     python garmin_verify.py
"""

import getpass
import json
import os
import sys
from pathlib import Path

import requests

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ACTIVITIES_PATH = "/activitylist-service/activities/search/activities"


def main():
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY") or getpass.getpass(
        "Garmin Personal API key (hidden): "
    )
    api_key = api_key.strip()
    if not api_key:
        sys.exit("No API key provided.")

    print("1. Exchanging API key for an access token...")
    try:
        response = requests.post(
            BASE_URL + TOKEN_PATH,
            headers={"Authorization": f"ApiKey {api_key}"},
            timeout=20,
        )
        response.raise_for_status()

        # Garmin currently returns the bearer token as plain text.
        token = response.text.strip().strip('"')
        if not token:
            sys.exit("Garmin returned an empty access token.")

        print("   Authentication successful.")
        print("2. Retrieving your five most recent activities...")
        response = requests.get(
            BASE_URL + ACTIVITIES_PATH,
            headers={"Authorization": f"Bearer {token}"},
            params={"start": 0, "limit": 5},
            timeout=20,
        )
        response.raise_for_status()
        activities = response.json()
        if not isinstance(activities, list):
            sys.exit("Unexpected activities response format (expected a list).")

        output = Path("garmin_activities.json")
        output.write_text(json.dumps(activities, indent=2), encoding="utf-8")
        print(f"   Success! Retrieved {len(activities)} activities.")
        print(f"   Full response saved to {output.resolve()}\n")

        for activity in activities:
            activity_type = activity.get("activityType") or {}
            name = activity.get("activityName", "Unnamed activity")
            when = activity.get("startTimeLocal", "Unknown date")
            kind = activity_type.get("typeKey", "unknown")
            duration = (activity.get("duration") or 0) / 60
            distance = (activity.get("distance") or 0) / 1000
            print(f"- {when} | {name} ({kind}) | {duration:.1f} min | {distance:.2f} km")

    except requests.HTTPError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            sys.exit(f"Garmin returned HTTP {status}: check your API key and access permissions.")
        sys.exit(f"Garmin returned HTTP {status} for {exc.request.method} request.")
    except requests.RequestException as exc:
        sys.exit(f"Network error: {exc.__class__.__name__}: {exc}")
    except ValueError as exc:
        sys.exit(f"Could not parse Garmin response: {exc}")


if __name__ == "__main__":
    main()
