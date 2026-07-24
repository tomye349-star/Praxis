#!/usr/bin/env python3
"""
Recurring script (run by GitHub Actions) that refreshes the SBHS Student
Portal access token, fetches the timetable, and stores it in the private
dashboard backend. Run sbhs_bootstrap.py once yourself first to seed the
initial refresh token — this script takes over from there automatically.

Required environment variables:
  SBHS_CLIENT_ID
  SBHS_CLIENT_SECRET   (omit/leave blank if your app is "public", not confidential)
  DASH_API_URL         e.g. https://personal-dashboard-api.xxx.workers.dev
  DASH_TOKEN           same token the dashboard frontend uses

Important: SBHS refresh tokens are single-use — every refresh returns a NEW
refresh token and invalidates the old one. This script stores the new one
back into the worker immediately after refreshing, before doing anything
else, so a failure later in the script never strands us with a token we've
already burned.

Note on endpoints: see the comment in sbhs_bootstrap.py about the two
different OAuth URL pairs in the docs. Keep TOKEN_URL in sync with whichever
pair actually worked during bootstrap.
"""
import os
import sys
import json

import requests

TOKEN_URL = "https://auth.sbhs.net.au/token"
# Alternate, if the above 404s: "https://student.sbhs.net.au/api/token"

API_BASE = "https://student.sbhs.net.au/api"

CLIENT_ID = os.environ["SBHS_CLIENT_ID"]
CLIENT_SECRET = os.environ.get("SBHS_CLIENT_SECRET", "")
DASH_API_URL = os.environ["DASH_API_URL"].rstrip("/")
DASH_TOKEN = os.environ["DASH_TOKEN"]
DASH_HEADERS = {"Authorization": f"Bearer {DASH_TOKEN}", "Content-Type": "application/json"}


def kv_get(key):
    resp = requests.get(f"{DASH_API_URL}/api/kv/{key}", headers=DASH_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json().get("value")


def kv_set(key, value):
    resp = requests.post(
        f"{DASH_API_URL}/api/kv/{key}",
        headers=DASH_HEADERS,
        data=json.dumps({"value": value}),
        timeout=30,
    )
    resp.raise_for_status()


def refresh_access_token():
    refresh_token = kv_get("sbhs_refresh_token")
    if not refresh_token:
        sys.exit(
            "No SBHS refresh token stored yet — run sbhs_bootstrap.py once, locally, "
            "to log in and seed it before this script can do anything."
        )

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET

    resp = requests.post(TOKEN_URL, data=data, timeout=30)
    if not resp.ok:
        sys.exit(f"Token refresh failed ({resp.status_code}): {resp.text}")

    tokens = resp.json()
    new_refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not access_token:
        sys.exit(f"No access_token in refresh response: {tokens}")

    # Store the new refresh token FIRST, before we do anything else that could
    # fail — the old one is already dead the moment SBHS issued this response.
    if new_refresh_token:
        kv_set("sbhs_refresh_token", new_refresh_token)

    return access_token


def fetch_timetable(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    day = requests.get(f"{API_BASE}/timetable/daytimetable.json", headers=headers, timeout=30)
    day.raise_for_status()
    full = requests.get(f"{API_BASE}/timetable/timetable.json", headers=headers, timeout=30)
    full.raise_for_status()
    return day.json(), full.json()


def normalise(day_raw, full_raw):
    """
    Best-effort transform into the shape the dashboard expects
    ({today, week, lookAhead}). The Student Portal API docs Tom pasted don't
    include a sample response body for these two endpoints, so the exact
    field names here are an educated guess based on common timetable API
    shapes, not a guarantee. The raw responses are always stored alongside
    this (see main()) specifically so this function can be corrected later
    by inspecting real output without needing to touch the OAuth/networking
    code at all.
    """
    def extract_periods(raw):
        # Try a few plausible shapes defensively rather than assuming one.
        periods = raw.get("periods") or raw.get("timetable") or raw.get("bells") or []
        out = []
        for p in periods if isinstance(periods, list) else []:
            out.append({
                "time": p.get("time") or p.get("start") or p.get("period") or "",
                "subject": p.get("subject") or p.get("title") or p.get("name") or "Unknown",
                "location": p.get("room") or p.get("location") or "",
            })
        return out

    try:
        today = extract_periods(day_raw)
    except Exception:
        today = []

    try:
        # `full_raw` is generally a list of days for the week/cycle; normalise
        # defensively since we don't have a confirmed sample shape.
        week = []
        days = full_raw if isinstance(full_raw, list) else full_raw.get("days", [])
        for d in days:
            week.append({
                "day": d.get("day") or d.get("date") or "",
                "items": extract_periods(d),
            })
    except Exception:
        week = []

    return {"today": today, "week": week, "lookAhead": []}


def main():
    access_token = refresh_access_token()
    day_raw, full_raw = fetch_timetable(access_token)

    # Always store the raw responses — guaranteed correct, and the fallback
    # for fixing normalise() above without re-running the OAuth dance.
    kv_set("sbhs_raw_daytimetable", json.dumps(day_raw))
    kv_set("sbhs_raw_timetable", json.dumps(full_raw))

    timetable = normalise(day_raw, full_raw)
    timetable["generatedAt"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    kv_set("timetable", json.dumps(timetable))

    print(f"Synced timetable: {len(timetable['today'])} period(s) today, {len(timetable['week'])} day(s) this cycle")
    if not timetable["today"] and not timetable["week"]:
        print(
            "Heads up: got 0 periods back from normalise() — the raw response "
            "shape probably didn't match the guessed field names. Check "
            "sbhs_raw_daytimetable / sbhs_raw_timetable in the dashboard "
            "backend (GET /api/kv/sbhs_raw_daytimetable) and adjust "
            "normalise() in this script to match."
        )


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        print(f"HTTP error: {e} — {body}", file=sys.stderr)
        sys.exit(1)
