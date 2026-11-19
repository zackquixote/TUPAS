# TUPAS — System log review (admin view)

> **Status: fixes shipped 19 Nov 2026.** The P1–P3 items in this review are implemented
> (durable `logs/*.log`, undeletable trail, coverage for denials/shield/admin edits/
> deletes/exports, request-id join, retention, dashboard filters + CSV, `/v1/` probe).
> The review findings below are kept as the record of *why*.

**Review date:** 19 Nov 2026, 01:35–01:45 (Asia/Manila)
**Reviewed as:** `admin` (`is_staff` + `is_superuser`), signed in through `/login/` and `/admin/`
**Data sources:** runtime access log (`.zwork/background/*.log`), application audit trail (`tupas.ActivityLog` in `db.sqlite3`), code-side loggers, and the admin UI itself
**Application code changed:** none

---

## 1. What "system log" means in this project

There are three separate layers, and they do not talk to each other:

| Layer | Where it lives | Written by | Retention |
|---|---|---|---|
| Runtime access log | `.zwork/background/*.log` (7 files) | Django dev server (`runserver`) | None — one file per server start, no rotation |
| Audit trail | `tupas_activitylog` table | `services/activity.record_activity()` + auth signals | None — grows forever |
| Code loggers | Console only | `tupas.services.shield` (`logger.warning` on bans/blocks) | None — no handler, lost on restart |

There is no `LOGGING` setting in `config/settings.py`, so Django's default applies: the `django` logger gets two **console** handlers under `DEBUG`, and `ADMINS` is `[]`. Verified at runtime:

```
<root>          level=WARNING  handlers=0
django          level=INFO     handlers=2   (console, DEBUG-only)
django.request  level=NOTSET   handlers=0
tupas.shield    level=NOTSET   handlers=0
ADMINS=[]
DEBUG=True
```

Consequence: the only durable record of what happens in this app is the single `ActivityLog` table. Everything else — 500 tracebacks, shield bans, CSRF rejections, throttled requests — exists only in a console buffer and disappears when the server stops.

---

## 2. Runtime traffic snapshot

Window: **18 Nov 20:38:51 → 19 Nov 01:35:11** (4 h 57 m), 7 log files.

| Metric | Value |
|---|---|
| Requests | 1,402 (avg 284/h) |
| Bytes served | 17.9 MB |
| Distinct paths | 85 |
| `200` | 865 (61.7 %) |
| `304` | 214 (15.3 %) |
| `404` | 279 (19.9 %) |
| `302` | 35 |
| `301` / `403` / `500` | 7 / 1 / 1 |
| Static assets | 499 (35.6 %) |
| `/health` | 286 (20.4 %) — of which **266 are 404s** |
| `/sw.js` | 227 (16.2 %) |
| POSTs | 55 |
| Broken pipes | 11 (analysed in §2.1) |
| Server start markers | 5 (plus 2 autoreloads) |

Login POSTs by status: **10 × 302** (success), **6 × 200** (form redisplayed = failure), **1 × 403** (CSRF).
Other POSTs: 4 × logout, 1 × exam submit, 3 × `/api/progress/`, 1 × study-buddy upload, 28 × AI generate (21 × 200, 7 × 302 to login).

Top paths: `/health` 273, `/sw.js` 227, `/` 113, `style.css` 96, `/ai/generate/` 88, `/login/` 54, `/static/manifest.json` 45.

### What actually happened in those 5 hours

- **20:38 – 22:57 — build session.** Four decks created (`Digestive System`, two `Unit-4…` imports, `Social_and_Professional_Issues…`), 72 review items, one exam submitted (8/8, 100 %), repeated AI generation.
- **00:06 – 01:35 — UI review session.** Many `/login/`, `/register/`, `/users/` visits, one 500, asset reloads with cache-busting params (`?v=ui3`, `?v=admin`).
- **00:56 / 00:58 / 01:00 / 01:28 — admin session.** Logout, two logins, logout, then a failed login.

---

## 2.1 The 11 broken pipes

Django's dev server logs this line at **INFO**, not ERROR, from `WSGIServer.handle_error` when the exception is `BrokenPipeError`, `ConnectionAbortedError` or `ConnectionResetError` (`django/core/servers/basehttp.py:79-83`). Translation: **the client hung up and the server noticed on that socket.** It is not a crash, not a 5xx, and not an application error — the thread dies, the threaded server (`ThreadedWSGIServer`, `daemon_threads=True`) keeps serving, and `ActivityLog` is correctly untouched (transport, not an account or content event).

All 11 came from `127.0.0.1`, each on a different ephemeral port (52047–64704), i.e. 11 separate connections, at 2.2 per hour (0.8 % of requests).

| # | Time | Immediately around it | Cluster |
|---|---|---|---|
| 1 | 20:42:31 | `/ai/generate/` 302; `/health` 404 four seconds earlier | prober / navigation |
| 2 | 20:46:21 | `/login/` 200; `/health` 404 six seconds later | prober / navigation |
| 3 | 22:02:17 | two `POST /ai/generate/` 200, 39,993 bytes each | aborted large transfer |
| 4 | 22:52:33 | **same second:** `/health` 301, `/health/` 200 ×2, `/v1/models` 404 | prober (smoking gun) |
| 5 | 22:57:14 | **same second:** `/health/` 200, `/ai/generate/` 302 | prober |
| 6 | 22:59:08 | 0.4 s after `/health/` 200 | prober |
| 7 | 00:50:00 | **same second** as `/health/` 200 | prober |
| 8 | 00:56:06 | **same second:** `/study-buddy/` 200, `/sw.js` | navigation / service worker |
| 9 | 00:57:58 | **same second** as `/health/` 200 | prober |
| 10 | 01:27:59 | dashboard load, `bootstrap.min.css` 232,803 bytes | navigation / service worker |
| 11 | 01:28:00 | second dashboard load one second later, same 232 KB CSS | navigation / service worker |

**Cluster A — the 30-second prober (6 of 11).** Something outside this repository polls the server every 30.00 seconds (verified: the only gaps in the cadence are second-rounding 30 → 31 s). The app never calls `/health` or `/v1/models` — a grep of `*.js`, `*.html`, `*.py` finds them only in `shield.py`'s throttle-exempt list. The in-app 500 ms `setInterval` is the offline Pomodoro timer, not a network check.

The poller's biography:

- **20:38:57 → 22:51:33** — `GET /health` every 30 s, **266 × 404**, each with a 6,158-byte `DEBUG` 404 page. Wrong URL (no trailing slash).
- **22:52:03 → 22:55:03** — eight ticks of `GET /health` → 301, `GET /health/` → 200, **and `GET /v1/models` → 404 (9,361 bytes)**, then the 30 s loop stops.
- After that, only stray `/health/` 200s (22:57, 22:59, 00:50, 00:56, 00:57) — and five of the broken pipes land on those ticks.

`/health` + `/v1/models` on port 8000 is the readiness probe of an **OpenAI-compatible server** (vLLM, LiteLLM and friends default to 8000). Either the process supervisor that launched `runserver` (these logs live in `.zwork/background/`) or a local LLM tool is pointed at Django. Mechanism of the pipe error: Django's dev server speaks HTTP/1.1 with keep-alive whenever `Content-Length` is set (`ServerHandler.cleanup_headers`). A prober that sends one request, reads the status/body, and closes — especially with a reset rather than `Connection: close` — leaves the server's next `readline()` on that socket raising `ConnectionResetError`, which Django reports as a broken pipe. A client that does not drain a 6–9 KB `DEBUG` 404 body produces the same error mid-write.

**Cluster B — navigation and the service worker (5 of 11).** `templates/sw.js` fetches navigations (`await fetch(request)`) and refreshes the static cache in the background. When the tab unloads or the user clicks away, in-flight fetches are aborted and the server sees a dead socket. That matches the AI-generator pages (~40 KB), `/study-buddy/` (17 KB), and the two dashboard loads at 01:27:59–01:28:00 that each pulled `bootstrap.min.css` (232,803 bytes) seconds before the 01:28:04 logout.

**`DEBUG=True` amplifies both clusters.** Every 404 is a 6–15 KB debug page. A probe that only wants the status code and closes will EPIPE the server mid-body. With `DEBUG=0` the 404 body is a few hundred bytes and most of these writes complete before the client is gone.

**Impact:** none. Local-only, no 5xx, no traceback, no data, no audit-trail effect, no throttle ( `/health/` is in `EXEMPT_PREFIXES`). It is noise that buries real signal in a log used for incident analysis — 11 lines that look like failures and are not.

**What to do**

1. Identify the 30 s poller and point it at `/health/` (both `/health` and `/health/` now return the 28-byte 200). Have it send `Connection: close` or read the full body so the server is not left on a reset keep-alive socket.
2. Stop probing `/v1/models` on the Django port — it is now a shield probe (small 404, logged). If a local LLM stack expects 8000, move one of the two; Django should not answer OpenAI-compatible discovery.
3. Run with `DJANGO_DEBUG=0` when not editing code — smaller 404 bodies, fewer mid-write EPIPEs, less log noise.
4. Do **not** record broken pipes in `ActivityLog`. They are transport events.
5. Optional: a logging filter on `django.server` to drop `Broken pipe` INFO lines if they drown real errors (now installed on the console handler).
6. Minor: `EXEMPT_PREFIXES` contains `/health/` but not `/health`, so the 266 misconfigured 404s counted toward the 240 req/60 s throttle. Harmless at this rate; fix the URL rather than widening the exemption.

> Implementation note: setting `Connection: close` on the health `JsonResponse` is not possible on the dev server — wsgiref rejects hop-by-hop headers in `start_response`. The working fix is a real 200 on both `/health` and `/health/` (no 6 KB 404 body).

---

## 3. Audit trail state

Only **5 rows** existed before this review, covering **32 minutes** (00:56:58 → 01:28:27):

| When | User | Action | Detail | IP |
|---|---|---|---|---|
| 00:56:58 | admin | LOGOUT | Signed out | 127.0.0.1 |
| 00:58:29 | admin | LOGIN | Signed in | 127.0.0.1 |
| 01:00:05 | admin | LOGIN | Signed in | 127.0.0.1 |
| 01:28:04 | admin | LOGOUT | Signed out | 127.0.0.1 |
| 01:28:27 | — | LOGIN_FAILED | `Username: lourdianrentinoakol@gmail.com` | 127.0.0.1 |

All rows: one IP, two browser builds — of the five original rows, the 01:00:05 login reports Chrome 148 and the other four Chrome 157, so at least two clients were involved.

The table was created at **00:39** (migration `0006_activitylog`), and the first row is 00:56. Every earlier event is unrecoverable: the runtime log proves **8 successful logins and 5 failed logins** happened before that point with no audit record. The trail literally starts mid-session — row 1 is a `LOGOUT` whose `LOGIN` was never captured.

Content activity is equally absent: 4 decks, 72 items, 1 handout, 1 exam, 2 progress marks exist in the database, and `DECK_CREATE`, `DOCUMENT_UPLOAD`, `EXAM_SUBMIT`, `ITEM_CREATE` all have **0 rows**. That is explained by timing (all writes happened before 00:39), not by a code gap — but it means the trail has never actually been exercised by a content write, and the admin dashboard has never shown one.

---

## 4. Findings

### P1 — No durable application log outside the database

`django.request` and `django.security` have no handlers of their own; `tupas.shield` has none at all. With `DEBUG=0` the `django` console handler is filtered out, so a 500 or a shield ban in production produces **no record anywhere**. `ADMINS=[]` means `mail_admins` has no recipients either.

`manage.py security_check` passes 17/17 and does not test logging at all — the hardening audit has a blind spot exactly where incident response needs it.

**Fix:** add a `LOGGING` dict: `RotatingFileHandler` on `logs/tupas.log` (10 MB × 5) at INFO, a separate `logs/security.log` at WARNING for `tupas.shield` + `django.security`, and `mail_admins` for `django.request` 5xx. Add a check to `security_check`.

### P1 — The audit trail is not append-only

`ActivityLogAdmin` disables add and change, but **not delete**:

- changelist renders the `delete_selected` bulk action,
- `GET /admin/tupas/activitylog/<id>/delete/` returns **200** (confirmation page),
- `GET .../add/` returns 403 (correctly blocked).

So a superuser can erase history from the UI in two clicks, and the model has no protective FK or hash chain. The table's own id sequence already shows the pattern: this review removed synthetic rows 6–10, leaving a visible gap that nothing detects.

**Fix:** `has_delete_permission → False`, drop the delete permission from the admin group, and (for real assurance) chain rows with `prev_hash`/`hash` or ship them to an append-only sink.

### P1 — Coverage blind spots in the audit trail

`ActivityLog.Action` has 13 values, all auth plus coarse content events. Against ~45 routes in `tupas/urls.py`, these produce **no audit row at all**:

- **403 / CSRF failures** — the runtime log shows one at 21:21:39; the audit trail has nothing.
- **429 throttle and shield bans** — `shield.py` logs to a logger with no handler, and never to the DB.
- **Django-admin content edits and deletions** — every `Deck`, `ReviewItem`, `QuizChoice` change made in `/admin/` bypasses `record_activity` entirely.
- **Deletions of any kind** — there is no `DECK_DELETE`, `ITEM_DELETE` covers items only, and there is **no `USER_DELETE`**. The database shows users 2–5 and decks 1 and 3 are gone, with zero rows explaining when or by whom.
- Deck clone / merge / archive / pin / export, handout edit/delete, study-buddy chat, profile edits.

`record_activity()` also swallows every exception (`except Exception: return None`) with no counter, so a schema or DB failure silently stops the audit trail without anyone noticing.

**Fix:** add actions for `DENIED`, `RATE_LIMITED`, `ADMIN_EDIT`, `DECK_DELETE`, `USER_DELETE`, `EXPORT`; hook the shield middleware and `PermissionDenied`; override `save_model`/`delete_model` on the content admins; log a warning when `record_activity` fails.

### P2 — The recorded IP is meaningless behind a proxy

`record_activity` reads `request.META['REMOTE_ADDR']` only, and never `X-Forwarded-For`. This project sits in `C:\xampp\htdocs`, i.e. it is meant to run behind Apache — at which point **every row will read `127.0.0.1`**, exactly as all 7 current rows do. The trail would still look populated while being useless for attribution.

**Fix:** resolve the client IP through an explicit trusted-proxy list (or `USE_X_FORWARDED_HOST` + a small middleware), and store both the peer and the resolved client address.

### P2 — PII in a free-text column

`LOGIN_FAILED` stores the attempted identifier verbatim: `Username: lourdianrentinoakol@gmail.com`. Usernames in this app are often email addresses, so the log accumulates personal data (RA 10173 territory) in a 255-char text field that is displayed in full on the admin dashboard and searchable in Django admin.

**Fix:** store a masked/hashed identifier in a dedicated column, keep `detail` for human-readable context, and never persist password material (it does not today — good).

### P2 — Attribution is destroyed when a user is deleted

`user = ForeignKey(..., on_delete=SET_NULL, null=True)`. Delete an account and every row it owns becomes anonymous with no way to tell who it was. Combined with the missing `USER_DELETE` action, deleting a user is effectively an audit eraser.

**Fix:** add `username_snapshot` (and `user_id_snapshot`) written at insert time, or switch to `PROTECT`.

### P2 — No retention, rotation, or pruning anywhere

- `tupas_activitylog` grows unbounded in SQLite; there is no `prune_activity` management command (only `security_check`).
- The 7 dev-server logs are unrotated, one per start, and contain full tracebacks and request bodies' paths.
- `django_session` is never swept: **4 of the 7 live sessions point at user ids 2 and 3, which no longer exist**. Sessions live 14 days (`SESSION_COOKIE_AGE=1209600`), so deleted users leave working-looking rows behind.

**Fix:** a `prune_activity --days 180` command plus a documented retention policy, log rotation for files, and a scheduled `clearsessions`.

### P2 — The two log sources cannot be joined

The access log line has no user, no session, no request id. When the trail is ambiguous, the runtime log cannot resolve it. Concrete example: at 01:28:04 admin logs out, 01:28:07 and 01:28:20 `GET /users/` return 302 (unauthenticated), 01:28:27 a login fails, and then 01:29:56 `GET /users/` returns **200** with no successful login POST in between. The most likely explanation is a second browser session that was still authenticated (7 session rows exist, several for the same user), but nothing in either log can prove it.

**Fix:** request-id middleware echoed in the access line and stored on `ActivityLog`; include `user=` in the server log format; log a session-key fingerprint on login.

### P3 — Health check is pointed at the wrong URL

`/health` returns **404, 266 times** — 20 % of all logged requests — because the route is `/health/`. After 22:52 the probe follows the 301 and succeeds, but the noise already dominates the log. Any alerting built on this log would be reading 404s.

**Fix:** point the monitor at `/health/`; optionally filter `/health/` and `/sw.js` out of the access log (33 % of volume combined) so real traffic is visible.

### P3 — Admin log UX

The custom dashboard shows **20 rows per page, no filter, no search, no export**, and omits `user_agent`. Django admin has filters and a date hierarchy but a different column set, allows row deletion, and is a separate surface. Two half-features instead of one usable audit view.

**Fix:** one log page with action/user/date filters, full-text search on `detail`, CSV export, and a per-row detail view showing user agent.

### P3 — Cosmetic and housekeeping

- Rows read "Logged in" / "Signed in" — action and detail duplicate each other.
- Stat cards say "1 Admins", "1 Study Buddy handouts" (labels are hard-coded plural).
- Test accounts `__browser_probe__` and `ui_preview` are **active** and appear under "Recent registrations" as if they were students.
- `admin`'s `StudentProfile.role` is `STUDENT` while `is_staff=True`. Not a privilege bug (`effective_role` checks `is_staff` first) but it makes any role-based report wrong.

---

## 5. Anomalies assessed (none is an intrusion)

| Event | Reading | Confidence |
|---|---|---|
| `500 GET /profile/` at 22:57:40 | `AttributeError: 'str' object has no attribute 'first'` at `views/profile.py:28`. Current code uses `[:1]` and returns 200 — already fixed. Visible only because `DEBUG=True` rendered a 72 KB traceback page. | High |
| `403 POST /login/` at 21:21:39 | 11 s after a successful login POST. Most consistent with a re-submitted form carrying the pre-login CSRF token (Django rotates it on login). Not recorded in the audit trail. | Medium |
| `404 GET /login/admin` ×2 | Operator guessing an admin URL. Real one is `/admin/`. | High |
| `404 GET /v1/models` ×7 | An OpenAI-compatible client pointed at port 8000, polling alongside `/health` every 30 s (see §2.1). Harmless locally; worth turning off. | High |
| `LOGIN_FAILED` for `lourdianrentinoakol@gmail.com` | Unknown account, single attempt, immediately followed by `GET /register/`. Looks like lockout-path testing, not credential stuffing. No lockout/throttle event exists anywhere to compare against. | Medium |
| `GET /users/` 200 with no login POST | Second authenticated session (stale or concurrent). No single-session policy is enforced. | Low |
| 11 broken pipes | Client disconnects: 6 on the 30 s `/health` + `/v1/models` prober, 5 on navigation/service-worker aborts of large transfers. Full analysis in §2.1. Noise, not errors. | High |

---

## 6. Priority order

1. **Add `LOGGING`** — file + rotation + `mail_admins` for 5xx; add a logging check to `security_check`. *(1 file of settings, highest payoff.)*
2. **Make the trail undeletable** — `has_delete_permission = False`, then hash-chain or ship off-box.
3. **Close the coverage gaps** — 403/429/ban, Django-admin edits, deletions (`USER_DELETE`, `DECK_DELETE`), exports.
4. **Fix attribution** — proxy-aware IP, `username_snapshot`, mask identifiers in `LOGIN_FAILED`.
5. **Add retention** — `prune_activity`, log rotation, `clearsessions`.
6. **Make the sources joinable** — request id + `user=` in the access log.
7. **Point the health probe at `/health/`** and quiet the access log. *(Done: both URL forms return 200; `/v1/` is a shield probe; broken-pipe lines filtered from the console.)*

---

## 7. Reproduce

```powershell
# runtime log aggregation (status codes, paths, 4xx/5xx, broken pipes)
venv\Scripts\python.exe .freebuff\log_parse.py

# audit trail + platform counts
venv\Scripts\python.exe .freebuff\log_overview.py

# admin session, page status codes, query counts, delete rights
venv\Scripts\python.exe .freebuff\admin_view_check.py
venv\Scripts\python.exe .freebuff\verify_logging_config.py

# hardening audit (no logging coverage)
venv\Scripts\python.exe manage.py security_check
```

Admin-facing views verified with a real admin session: `/`, `/users/`, `/users/1/`, `/profile/`, `/decks/6/`, `/admin/`, `/admin/tupas/activitylog/` → 200; `/study-buddy/` → 302 (admins are redirected away from student tools, by design); anonymous `/users/` → 302; non-staff `/users/` → 403, non-staff `/admin/` → 302. Dashboard renders in 19 queries; the Django changelist in 7.

---

## 8. Changes made during this review

- Test-client checks created **3 synthetic `LOGIN` rows** (empty user agent) and **5 temporary sessions**. All were removed; the audit table is back to its original content.
- One **real** admin sign-in was performed through the UI as requested, and logged out afterwards, leaving two legitimate rows: `01:43:18 LOGIN` and `01:44:26 LOGOUT`.
- Current state: **7 audit rows** (5 original + 2 from this review), **7 sessions** (unchanged from before the review).
- No source file, migration, or setting was modified.
