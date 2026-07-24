// Personal dashboard API — runs on Cloudflare Workers (free tier) + D1 (free tier).
// Handles: reading assignments/study state, logging study minutes, adding
// assignments manually (until Canvas sync exists), and archiving old ones.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Authorization",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    // Every request (including reads) needs the shared token — this is
    // personal study/assignment data, not something to leave open.
    const auth = request.headers.get("Authorization") || "";
    const token = auth.replace(/^Bearer\s+/i, "");
    if (!env.DASH_TOKEN || token !== env.DASH_TOKEN) {
      return json({ error: "unauthorized" }, 401, cors);
    }

    try {
      if (url.pathname === "/api/state" && request.method === "GET") {
        return json(await getState(env.DB), 200, cors);
      }

      if (url.pathname === "/api/assignments" && request.method === "POST") {
        const body = await request.json();
        if (!body.subject || !body.title) {
          return json({ error: "subject and title are required" }, 400, cors);
        }
        const id = crypto.randomUUID();
        await env.DB.prepare(
          "INSERT INTO assignments (id, subject, title, due_date, status) VALUES (?, ?, ?, ?, 'active')"
        )
          .bind(id, body.subject, body.title, body.dueDate || null)
          .run();
        return json({ id }, 200, cors);
      }

      if (url.pathname === "/api/log" && request.method === "POST") {
        const body = await request.json();
        const minutes = Number(body.minutes);
        if (!minutes || minutes <= 0) {
          return json({ error: "minutes must be a positive number" }, 400, cors);
        }
        // Snapshot the subject/title onto the log row itself at the moment
        // it's logged, rather than only linking via assignment_id. That way
        // subject totals and log history stay correct forever, even after
        // the assignment is later edited, archived, or deleted entirely.
        let subject = null;
        let assignmentTitle = null;
        if (body.assignmentId) {
          const a = await env.DB.prepare("SELECT subject, title FROM assignments WHERE id = ?")
            .bind(body.assignmentId)
            .first();
          if (a) { subject = a.subject; assignmentTitle = a.title; }
        }
        const id = crypto.randomUUID();
        await env.DB.prepare(
          "INSERT INTO study_logs (id, assignment_id, minutes, note, subject, assignment_title) VALUES (?, ?, ?, ?, ?, ?)"
        )
          .bind(id, body.assignmentId || null, minutes, body.note || null, subject, assignmentTitle)
          .run();
        return json({ id }, 200, cors);
      }

      const archiveMatch = url.pathname.match(/^\/api\/archive\/([^/]+)$/);
      if (archiveMatch && request.method === "POST") {
        await env.DB.prepare("UPDATE assignments SET status = 'archived' WHERE id = ?")
          .bind(archiveMatch[1])
          .run();
        return json({ ok: true }, 200, cors);
      }

      const unarchiveMatch = url.pathname.match(/^\/api\/unarchive\/([^/]+)$/);
      if (unarchiveMatch && request.method === "POST") {
        await env.DB.prepare("UPDATE assignments SET status = 'active' WHERE id = ?")
          .bind(unarchiveMatch[1])
          .run();
        return json({ ok: true }, 200, cors);
      }

      // Edit (PATCH) or delete (DELETE) a single assignment, active or
      // archived. Editing only touches fields that were actually sent, so a
      // client can PATCH just { dueDate } without clobbering subject/title.
      const assignmentIdMatch = url.pathname.match(/^\/api\/assignments\/([^/]+)$/);
      if (assignmentIdMatch && request.method === "PATCH") {
        const body = await request.json();
        const sets = [];
        const binds = [];
        if (typeof body.subject === "string" && body.subject.trim()) { sets.push("subject = ?"); binds.push(body.subject.trim()); }
        if (typeof body.title === "string" && body.title.trim()) { sets.push("title = ?"); binds.push(body.title.trim()); }
        if ("dueDate" in body) { sets.push("due_date = ?"); binds.push(body.dueDate || null); }
        if (!sets.length) {
          return json({ error: "nothing to update" }, 400, cors);
        }
        binds.push(assignmentIdMatch[1]);
        await env.DB.prepare(`UPDATE assignments SET ${sets.join(", ")} WHERE id = ?`)
          .bind(...binds)
          .run();
        return json({ ok: true }, 200, cors);
      }
      if (assignmentIdMatch && request.method === "DELETE") {
        await env.DB.prepare("DELETE FROM assignments WHERE id = ?")
          .bind(assignmentIdMatch[1])
          .run();
        // study_logs rows are left in place (they carry their own subject/
        // assignment_title snapshot now, so history and subject totals stay
        // intact) — only assignment_id becomes a dangling reference, which
        // getState() already handles by preferring the snapshot fields.
        return json({ ok: true }, 200, cors);
      }

      // Edit (PATCH) or delete (DELETE) a single logged study-time entry —
      // lets an accidental or wrong-length log get corrected or removed
      // instead of permanently skewing that subject's total. Editing only
      // touches minutes/note; the log stays linked to whatever assignment
      // (or "General") it was originally logged against.
      const logIdMatch = url.pathname.match(/^\/api\/logs\/([^/]+)$/);
      if (logIdMatch && request.method === "PATCH") {
        const body = await request.json();
        const sets = [];
        const binds = [];
        if ("minutes" in body) {
          const minutes = Number(body.minutes);
          if (!minutes || minutes <= 0) {
            return json({ error: "minutes must be a positive number" }, 400, cors);
          }
          sets.push("minutes = ?"); binds.push(minutes);
        }
        if ("note" in body) { sets.push("note = ?"); binds.push(body.note || null); }
        if (!sets.length) {
          return json({ error: "nothing to update" }, 400, cors);
        }
        binds.push(logIdMatch[1]);
        await env.DB.prepare(`UPDATE study_logs SET ${sets.join(", ")} WHERE id = ?`)
          .bind(...binds)
          .run();
        return json({ ok: true }, 200, cors);
      }
      if (logIdMatch && request.method === "DELETE") {
        await env.DB.prepare("DELETE FROM study_logs WHERE id = ?")
          .bind(logIdMatch[1])
          .run();
        return json({ ok: true }, 200, cors);
      }

      // Generic key/value store — used for the timetable feed, the merged
      // fitness activity feed, and rotating OAuth refresh tokens (SBHS, later
      // Strava). Automation reads/writes its own credentials here instead of
      // ever needing to touch GitHub secrets.
      const kvMatch = url.pathname.match(/^\/api\/kv\/([^/]+)$/);
      if (kvMatch && request.method === "GET") {
        const row = await env.DB.prepare("SELECT value, updated_at FROM kv_store WHERE key = ?")
          .bind(kvMatch[1])
          .first();
        return json(row || { value: null, updated_at: null }, 200, cors);
      }
      if (kvMatch && request.method === "POST") {
        const body = await request.json();
        if (typeof body.value !== "string") {
          return json({ error: "value must be a string (e.g. JSON.stringify it first)" }, 400, cors);
        }
        await env.DB.prepare(
          "INSERT INTO kv_store (key, value, updated_at) VALUES (?, ?, datetime('now')) " +
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
        )
          .bind(kvMatch[1], body.value)
          .run();
        return json({ ok: true }, 200, cors);
      }

      // Upsert a batch of assignments keyed by sourceId (e.g. "canvas-12345")
      // so re-running the sync script never creates duplicates — it just
      // updates due dates/titles on the existing row.
      if (url.pathname === "/api/sync/assignments" && request.method === "POST") {
        const body = await request.json();
        const list = Array.isArray(body.assignments) ? body.assignments : [];
        let upserted = 0;
        for (const a of list) {
          if (!a.sourceId || !a.subject || !a.title) continue;
          await env.DB.prepare(
            "INSERT INTO assignments (id, subject, title, due_date, status, source_id) " +
              "VALUES (?, ?, ?, ?, 'active', ?) " +
              "ON CONFLICT(source_id) WHERE source_id IS NOT NULL DO UPDATE SET " +
              "subject = excluded.subject, title = excluded.title, due_date = excluded.due_date"
          )
            .bind(crypto.randomUUID(), a.subject, a.title, a.dueDate || null, a.sourceId)
            .run();
          upserted++;
        }
        return json({ upserted }, 200, cors);
      }

      return json({ error: "not found" }, 404, cors);
    } catch (err) {
      return json({ error: String(err && err.message ? err.message : err) }, 500, cors);
    }
  },
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...cors },
  });
}

async function getState(DB) {
  const assignments = await DB.prepare(
    "SELECT * FROM assignments WHERE status = 'active' ORDER BY due_date ASC"
  ).all();
  const archive = await DB.prepare(
    "SELECT * FROM assignments WHERE status = 'archived' ORDER BY due_date DESC"
  ).all();
  const logs = await DB.prepare("SELECT * FROM study_logs ORDER BY logged_at DESC").all();

  const assignmentSubject = {};
  const assignmentTitleById = {};
  for (const a of assignments.results) { assignmentSubject[a.id] = a.subject; assignmentTitleById[a.id] = a.title; }
  for (const a of archive.results) { assignmentSubject[a.id] = a.subject; assignmentTitleById[a.id] = a.title; }

  const minutesByAssignment = {};
  const minutesBySubject = {}; // all-time, keyed by subject ("General" bucket for no-assignment logs)
  let weekMinutes = 0;
  const weekAgoMs = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const subjectsThisWeek = new Set();

  for (const log of logs.results) {
    if (log.assignment_id) {
      minutesByAssignment[log.assignment_id] =
        (minutesByAssignment[log.assignment_id] || 0) + log.minutes;
    }
    // Prefer the subject snapshotted on the log itself (survives the
    // assignment being edited/archived/deleted later); fall back to a live
    // lookup for older rows logged before that snapshot existed.
    const subj = log.subject || (log.assignment_id ? assignmentSubject[log.assignment_id] : null) || "General";
    minutesBySubject[subj] = (minutesBySubject[subj] || 0) + log.minutes;

    const loggedMs = new Date(log.logged_at.replace(" ", "T") + "Z").getTime();
    if (loggedMs >= weekAgoMs) {
      weekMinutes += log.minutes;
      subjectsThisWeek.add(subj);
    }
  }

  // Sorted alphabetically, with the catch-all "General" bucket pinned last
  // so real subjects always lead the list.
  const subjectTotals = Object.keys(minutesBySubject)
    .sort((a, b) => {
      if (a === "General") return 1;
      if (b === "General") return -1;
      return a.localeCompare(b);
    })
    .map((subject) => ({ subject, minutes: minutesBySubject[subject] }));

  const withMinutes = (a) => ({ ...a, loggedMinutes: minutesByAssignment[a.id] || 0 });

  // Recent logs get the same snapshot-first treatment so history reads
  // correctly ("Physics — Report") even for assignments that no longer exist.
  const recentLogs = logs.results.slice(0, 15).map((log) => ({
    ...log,
    subject: log.subject || (log.assignment_id ? assignmentSubject[log.assignment_id] : null) || "General",
    assignment_title: log.assignment_title || (log.assignment_id ? assignmentTitleById[log.assignment_id] : null) || null,
  }));

  const timetableRow = await DB.prepare("SELECT value FROM kv_store WHERE key = 'timetable'").first();
  const activitiesRow = await DB.prepare("SELECT value FROM kv_store WHERE key = 'activities'").first();

  let timetable = null;
  let activities = null;
  try { timetable = timetableRow ? JSON.parse(timetableRow.value) : null; } catch (e) { /* leave null on bad data */ }
  try { activities = activitiesRow ? JSON.parse(activitiesRow.value) : null; } catch (e) { /* leave null on bad data */ }

  return {
    assignments: assignments.results.map(withMinutes),
    archive: archive.results.map(withMinutes),
    weekMinutes,
    subjectsThisWeek: subjectsThisWeek.size,
    subjectTotals,
    recentLogs,
    timetable,
    activities,
  };
}
