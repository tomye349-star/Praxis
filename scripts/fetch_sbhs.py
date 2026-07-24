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


def _to_12h(t):
    """'09:25' -> '9:25am'. Falls back to the original string if it doesn't parse."""
    try:
        h, m = t.split(":")
        h = int(h)
        ampm = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return f"{h12}:{m}{ampm}"
    except Exception:
        return t


def _bell_time_lookup(bells):
    """
    bells: [{period/bell: '1', startTime: '09:25', ...}, ...] -> {'1': '09:25'}
    Kept in raw 24-hour form here (zero-padded, so it sorts correctly as a
    plain string) — converted to 12-hour display form separately, at the
    point of building each period's "time" field, so sorting never has to
    deal with "1:15pm" vs "10:25am" string-sorting wrong.
    """
    lookup = {}
    for b in bells or []:
        code = b.get("period") or b.get("bell")
        start = b.get("startTime") or b.get("time")
        if code and start:
            lookup[code] = start
    return lookup


def _subject_title_lookup(subjects):
    """
    `subjects` is keyed oddly (e.g. "11CHE 3") and periods reference the
    *shortTitle* ("CHE 3") instead, so index by shortTitle -> full subject name.
    Accepts either the dict-of-codes shape (daytimetable.json) or the
    list-of-subjects shape (timetable.json) — confirmed against real output
    from both endpoints.
    """
    lookup = {}
    values = subjects.values() if isinstance(subjects, dict) else (subjects if isinstance(subjects, list) else [])
    for s in values:
        if not isinstance(s, dict):
            continue
        short = s.get("shortTitle")
        if short:
            lookup[short] = s.get("subject") or s.get("title") or short
    return lookup


def _extract_day_periods(day_block, bells, subjects_lookup):
    """
    day_block shape (confirmed real output):
      { dayname, routine, rollcall: {...}, periods: { "1": {title, teacher, room, ...}, ... }, dayNumber }
    """
    if not isinstance(day_block, dict):
        return []
    periods = day_block.get("periods") or {}
    times = _bell_time_lookup(bells)  # raw "09:25"-style, for correct sorting
    out = []
    for period_code, info in periods.items():
        if not isinstance(info, dict):
            continue
        short_title = info.get("title") or "Unknown"
        raw_time = times.get(period_code, "")
        out.append({
            "time": _to_12h(raw_time) if raw_time else "",
            "subject": subjects_lookup.get(short_title, short_title),
            "location": info.get("room") or "",
            "_sort": raw_time or "99:99",
        })
    out.sort(key=lambda p: p["_sort"])
    for p in out:
        p.pop("_sort", None)
    return out


def normalise(day_raw, full_raw):
    """
    Transform into the shape the dashboard expects ({today, week, lookAhead}).

    Confirmed against real output from both endpoints (via a direct API call
    against the stored sbhs_raw_daytimetable / sbhs_raw_timetable values):

      daytimetable.json = {
        status, date,
        bells: [ {period, bell, startTime, endTime, type, bellDisplay}, ... ],
        timetable: {
          subjects: { "<yearcode>": {title, shortTitle, teacher, subject, ...} },
          timetable: { dayname, routine, rollcall, periods: {"<periodCode>": {title, teacher, room, ...}}, dayNumber },
          dayInfo: {...}
        }
      }

      timetable.json = {
        student, rollcall, advisor,
        subjects: [ {title, shortTitle, teacher, subject, ...}, ... ],
        days: <shape not confirmed yet — the full fortnight/cycle timetable>
      }

    The `today` mapping below is exact. The `week` mapping is still
    best-effort since `timetable.json`'s `days` field wasn't confirmed from a
    real sample — if it comes out empty, check `sbhs_raw_timetable` (GET
    /api/kv/sbhs_raw_timetable) for the real `days` shape and adjust the
    `days_block` handling below to match.
    """
    try:
        day_tt = (day_raw or {}).get("timetable") or {}
        subjects_lookup = _subject_title_lookup(day_tt.get("subjects"))
        today = _extract_day_periods(day_tt.get("timetable"), (day_raw or {}).get("bells"), subjects_lookup)
    except Exception:
        today = []

    try:
        week = []
        days_block = (full_raw or {}).get("days")
        full_subjects_lookup = _subject_title_lookup((full_raw or {}).get("subjects"))
        bells_for_week = (full_raw or {}).get("bells") or (day_raw or {}).get("bells")

        if isinstance(days_block, dict):
            entries = list(days_block.items())
        elif isinstance(days_block, list):
            entries = [(d.get("dayname") if isinstance(d, dict) else None, d) for d in days_block]
        else:
            entries = []

        for key, day_block in entries:
            periods = _extract_day_periods(day_block, bells_for_week, full_subjects_lookup)
            if not periods:
                continue
            day_label = (day_block.get("dayname") if isinstance(day_block, dict) else None) or key or ""
            week.append({"day": day_label, "items": periods})
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
