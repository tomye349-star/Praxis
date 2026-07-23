# Personal Dashboard

A single homepage that pulls together timetable, study, and training/health data — hosted free on GitHub Pages.

See `project-plan.md` (sent alongside this scaffold) for the full architecture and phase plan.

## What's in here right now

- `index.html` — the homepage template (coffee/cream style, colour-coded stat tiles and urgency badges), currently showing example data.
- `data/*.json` — placeholder data files in the shape the real integrations will eventually produce. Nothing live yet.
- `.github/workflows/sync-data.yml` — a scaffold GitHub Actions workflow that will later fetch from Canvas, the SBHS Student Portal, Strava, Garmin, and Huawei Health on a schedule and commit fresh JSON into `/data`. Right now it's a no-op placeholder.
- `scripts/` — empty for now; this is where the actual fetch scripts (Python) will live once each integration is built.

## Getting this live on GitHub Pages (5 minutes)

1. Go to github.com and create a new **empty** repository (no README/license needed) — e.g. name it `personal-dashboard`.
2. Upload the contents of this folder into the repo. Easiest way with no local git setup: on the repo's page, use **Add file → Upload files** and drag in everything from this folder (make sure the `.github` folder comes through — GitHub's uploader does support hidden folders, but if it gets dropped, add the workflow file manually afterwards via **Add file → Create new file** and pasting the path `.github/workflows/sync-data.yml`).
3. Go to **Settings → Pages**. Under "Build and deployment", set **Source: Deploy from a branch**, branch **main**, folder **/(root)**. Save.
4. GitHub will give you a URL like `https://<your-username>.github.io/personal-dashboard/` within a minute or two — that's your live site.
5. Later, when we wire up real data sources: add each API token/credential under **Settings → Secrets and variables → Actions** (never commit these directly into the repo).

## Next steps (see project-plan.md for detail)

1. Confirm SBHS Student Portal API access and build `scripts/fetch_sbhs.py`.
2. Build `scripts/fetch_canvas.py` (Canvas personal access token).
3. Build the study time-logging UI and the assignment archive view.
4. Build `scripts/fetch_strava.py`, `fetch_garmin.py`, and the best-effort Huawei Health integration, plus the de-duplication logic between Garmin and Huawei.
