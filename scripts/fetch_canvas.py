#!/usr/bin/env python3
"""
Fetches active assignments from Canvas and syncs them into the dashboard's
Cloudflare Worker backend (never writes to the public repo — this data stays
behind the worker's DASH_TOKEN).

Required environment variables:
  CANVAS_DOMAIN   e.g. sydneyboyshigh.instructure.com  (no https://, no trailing slash)
  CANVAS_TOKEN    Canvas personal access token
                  (Canvas -> Account -> Settings -> "+ New Access Token")
  DASH_API_URL    Your worker URL, e.g. https://personal-dashboard-api.xxx.workers.dev
  DASH_TOKEN      The same access token the dashboard frontend uses to connect
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests

CANVAS_DOMAIN = os.environ["CANVAS_DOMAIN"].rstrip("/")
CANVAS_TOKEN = os.environ["CANVAS_TOKEN"]
DASH_API_URL = os.environ["DASH_API_URL"].rstrip("/")
DASH_TOKEN = os.environ["DASH_TOKEN"]

CANVAS_BASE = f"https://{CANVAS_DOMAIN}/api/v1"
CANVAS_HEADERS = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
DASH_HEADERS = {"Authorization": f"Bearer {DASH_TOKEN}", "Content-Type": "application/json"}

# How many days overdue an assignment can be before we stop syncing it in.
# (Canvas doesn't auto-hide overdue assignments, and we don't want the
# dashboard cluttered with things from months ago that were never submitted.)
OVERDUE_CUTOFF_DAYS = 3


def canvas_get_all(path, params=None):
    """GET a Canvas endpoint, following pagination via the Link header."""
    url = f"{CANVAS_BASE}{path}"
    results = []
    while url:
        resp = requests.get(url, headers=CANVAS_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        results.extend(resp.json())
        params = None  # query params only apply to the first request; later pages carry their own
        url = None
        for link in resp.links.values():
            if link.get("rel") == "next":
                url = link["url"]
    return results


def get_active_courses():
    courses = canvas_get_all("/users/self/courses", params={"enrollment_state": "active", "per_page": 100})
    # Canvas can return courses you're no longer meant to see content for (e.g. concluded/restricted)
    return [c for c in courses if not c.get("access_restricted_by_date") and c.get("id")]


def get_assignments(course_id):
    return canvas_get_all(f"/courses/{course_id}/assignments", params={"per_page": 100})


def build_sync_payload(courses):
    synced = []
    now = datetime.now(timezone.utc)
    for course in courses:
        subject = course.get("course_code") or course.get("name") or "Unknown"
        for a in get_assignments(course["id"]):
            due_at = a.get("due_at")  # ISO 8601 string, or None if no due date set
            due_date = None
            if due_at:
                due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                if (now - due_dt).days > OVERDUE_CUTOFF_DAYS:
                    continue  # skip long-overdue assignments
                due_date = due_dt.date().isoformat()
            synced.append({
                "sourceId": f"canvas-{a['id']}",
                "subject": subject,
                "title": a.get("name", "Untitled assignment"),
                "dueDate": due_date,
            })
    return synced


def main():
    courses = get_active_courses()
    print(f"Found {len(courses)} active course(s)")

    synced = build_sync_payload(courses)
    print(f"Syncing {len(synced)} assignment(s) to the dashboard backend")

    resp = requests.post(
        f"{DASH_API_URL}/api/sync/assignments",
        headers=DASH_HEADERS,
        data=json.dumps({"assignments": synced}),
        timeout=30,
    )
    resp.raise_for_status()
    print("Done:", resp.json())


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        print(f"HTTP error: {e} — {body}", file=sys.stderr)
        sys.exit(1)
