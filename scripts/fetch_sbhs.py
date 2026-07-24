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


def _subject_lookup(subjects):
    """
    `subjects` is keyed oddly (e.g. "11CHE 3") and periods reference the
    *shortTitle* ("CHE 3") instead, so index by shortTitle -> {name, colour}.
    Accepts either the dict-of-codes shape (daytimetable.json) or the
    list-of-subjects shape (timetable.json) — confirmed against real output
    from both endpoints. `colour` is SBHS's own per-subject hex colour (no
    leading '#'), used so the weekly view's class chips match the same
    colour-coding students already see in the Student Portal / apps like
    HighHelp, instead of inventing our own.
    """
    lookup = {}
    values = subjects.values() if isinstance(subjects, dict) else (subjects if isinstance(subjects, list) else [])
    for s in values:
        if not isinstance(s, dict):
            continue
        short = s.get("shortTitle")
        if short:
            lookup[short] = {
                "name": s.get("subject") or s.get("title") or short,
                "colour": s.get("colour") or "",
            }
    return lookup


def _period_sort_key(period_code, raw_time):
    """
    Sort key for ordering a day's periods. Prefers an actual bell time when
    we have one (only available for "today"); falls back to a natural
    period-code order (RC first, then numeric periods in order, then
    anything else) for cycle days that have no bell times attached.
    """
    if raw_time:
        return (0, raw_time, "")
    if period_code == "RC":
        return (1, "", "")
    try:
        return (1, "%03d" % int(period_code), "")
    except (TypeError, ValueError):
        return (2, "", period_code or "")


def _describe_variation(var):
    """
    Returns (cancelled: bool, note: str or None) for one classVariations entry.

    Confirmed real shape (one real sample, a casual-teacher substitution):
      {period, year, title, teacher, type: "replacement", casual, casualSurname,
       roomFrom, roomTo}

    An outright cancellation (no casual covering it) hasn't shown up in a real
    sample yet, so it's handled defensively here: treated as cancelled/free
    whenever the type mentions "cancel", or the type is a "replacement" with
    no casual teacher actually assigned to cover it.
    """
    if not isinstance(var, dict):
        return False, None
    vtype = (var.get("type") or "").lower()
    casual_surname = var.get("casualSurname")
    room_to = var.get("roomTo")

    if "cancel" in vtype or ("replace" in vtype and not casual_surname):
        return True, "Cancelled — free period"

    notes = []
    if casual_surname:
        notes.append("Covering: " + casual_surname)
    if room_to:
        notes.append("Room changed to " + str(room_to))
    return False, " · ".join(notes) if notes else None


def _extract_day_periods(day_block, bells, subjects_lookup, variations=None, exclude_period_codes=None):
    """
    day_block shape (confirmed real output, identical for "today" and for
    each of the 10 cycle days under timetable.json's `days` field):
      { dayname, routine, rollcall: {...}, periods: { "1": {title, teacher, room, fullTeacher, ...}, ... }, dayNumber }

    variations (only available for "today", from daytimetable.json's top-level
    classVariations field) shape (confirmed real output):
      { "<periodCode>": {period, year, title, teacher, type, casual, casualSurname, roomFrom, roomTo} }

    `bells` may be None (cycle days don't carry bell times) — periods then
    sort by period-code order instead of by time, and "time" comes back "".
    `exclude_period_codes` lets callers drop e.g. roll call ("RC") from the
    weekly view, matching how HighHelp's cycle grid only shows real classes.
    """
    if not isinstance(day_block, dict):
        return []
    periods = day_block.get("periods") or {}
    times = _bell_time_lookup(bells) if bells else {}
    exclude = set(exclude_period_codes or [])
    out = []
    for period_code, info in periods.items():
        if not isinstance(info, dict) or period_code in exclude:
            continue
        short_title = info.get("title") or "Unknown"
        raw_time = times.get(period_code, "")
        var = (variations or {}).get(period_code)
        cancelled, note = _describe_variation(var)
        subj = subjects_lookup.get(short_title) or {"name": short_title, "colour": ""}
        out.append({
            "period": period_code,
            "time": _to_12h(raw_time) if raw_time else "",
            "subject": subj["name"],
            "colour": subj["colour"],
            "location": info.get("room") or "",
            "teacher": info.get("fullTeacher") or "",
            "cancelled": cancelled,
            "note": note,
            "_sort": _period_sort_key(period_code, raw_time),
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
          timetable: { dayname, routine, rollcall, periods: {"<periodCode>": {title, teacher, room, fullTeacher, ...}}, dayNumber },
          dayInfo: {...}
        },
        roomVariations: [...],
        classVariations: { "<periodCode>": {period, year, title, teacher, type, casual, casualSurname, roomFrom, roomTo} },
        shouldDisplayVariations: bool
      }

      timetable.json = {
        student, rollcall, advisor,
        subjects: [ {title, shortTitle, teacher, subject, colour, ...}, ... ],
        days: {
          "1": { dayname: "MonA", routine, rollcall, periods: {"<periodCode>": {...}}, dayNumber: "1" },
          "2": { dayname: "TueA", ... },
          ...
          "10": { dayname: "FriB", ... }
        }
      }

    `days` is confirmed real output — a 10-day fortnight (Week A Mon-Fri,
    then Week B Mon-Fri), each day using the exact same {periods: {...}}
    shape as "today". Cycle days never carry bell times or classVariations
    (those are today-only concepts), so week periods sort by period-code
    order and never show cancellations — only today's genuinely can be
    cancelled/substituted, since only today's schedule is actually running.
    Roll call ("RC") is deliberately excluded from `week`, matching how
    HighHelp's own cycle view only lists real classes.
    """
    try:
        day_tt = (day_raw or {}).get("timetable") or {}
        subjects_lookup = _subject_lookup(day_tt.get("subjects"))
        today = _extract_day_periods(
            day_tt.get("timetable"),
            (day_raw or {}).get("bells"),
            subjects_lookup,
            variations=(day_raw or {}).get("classVariations"),
        )
    except Exception:
        today = []

    try:
        week = []
        days_block = (full_raw or {}).get("days") or {}
        full_subjects_lookup = _subject_lookup((full_raw or {}).get("subjects"))

        # Keys are "1".."10" as strings — sort numerically so Week A Mon..Fri
        # comes before Week B Mon..Fri, not lexicographically ("1","10","2",...).
        def _day_key(k):
            try:
                return int(k)
            except (TypeError, ValueError):
                return 999

        for key in sorted(days_block.keys(), key=_day_key):
            day_block = days_block[key]
            if not isinstance(day_block, dict):
                continue
            periods = _extract_day_periods(
                day_block, bells=None, subjects_lookup=full_subjects_lookup, exclude_period_codes=["RC"]
            )
            if not periods:
                continue
            dayname = day_block.get("dayname") or ""  # e.g. "MonA", "TueB"
            weekday = dayname[:3] if len(dayname) > 1 else dayname
            cycle = dayname[3:] if len(dayname) > 3 else ""
            week.append({"day": dayname, "weekday": weekday, "cycle": cycle, "items": periods})
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
