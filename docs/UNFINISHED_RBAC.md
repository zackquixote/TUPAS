# TUPAS — Unfinished admin / RBAC / CSRF / security-header work

Hand this file to the next AI. Do **not** rewrite the existing RBAC system from scratch. The core is implemented and tested.

## What is already done (do not redo)

- Roles in `tupas/services/rbac.py`: `ADMIN` (`is_staff` or `is_superuser`), `TEACHER` (`StudentProfile.role`), `STUDENT`.
- Decorators: `role_required`, `admin_required`, `teacher_or_admin_required`.
- Views in `tupas/views/users.py`: `user_list`, `user_detail`, `set_user_role` (POST + `{% csrf_token %}`).
- URLs: `/users/`, `/users/<id>/`, `/users/<id>/role/`.
- Migration `tupas/migrations/0005_studentprofile_role.py`.
- `tupas/middleware.SecurityHeadersMiddleware`: nosniff, DENY, Referrer-Policy, Permissions-Policy, CSP, COOP, CORP, no-store for signed-in HTML, HSTS when `DEBUG=False`.
- CSRF middleware is installed; AJAX sends `X-CSRFToken` (`static/js/tupas.js`).
- Tests: `tupas/tests.py` class `RBACTests` (9 tests) — all pass. Full suite: **172/172 OK**.
- `python manage.py check` and `makemigrations --check` are clean.

---

## Unfinished work (implement these)

### 1. Teachers cannot see the Users link
`templates/base.html` (~line 143) hides **Users** and **Django admin** behind `{% if user.is_staff %}`. Teachers may open `/users/` but have no nav entry.

**Fix:** expose a template helper (context processor or `{% if %}` on `user.profile.role`) so:

- `ADMIN` (`is_staff`/superuser): Users + Django admin
- `TEACHER`: Users only (read-only directory)
- `STUDENT`: neither

Also add Users to the mobile bottom nav for teacher/admin.

### 2. No custom 403 (or 404/500) page
`role_required` and `can_edit_deck` raise `PermissionDenied`. Students/teachers get Django's plain 403.

**Fix:**
- `templates/403.html` (and `404.html`, `500.html`) matching `base.html`.
- Register in `config/urls.py`:
  ```python
  handler403 = 'tupas.views.errors.permission_denied'
  handler404 = 'tupas.views.errors.page_not_found'
  handler500 = 'tupas.views.errors.server_error'
  ```
- Return a friendly message: “Your account does not have permission to open that page.”

### 3. Templates still use `user.is_staff` instead of the role
Inconsistent with `effective_role()`. A `TEACHER` is not staff and misses admin-only UI; copy still says “teacher” in places.

Update:
- `templates/base.html` — Users / Django admin links
- `templates/tupas/dashboard.html` (~line 239) — empty-state “Add content in admin” vs “Check back after your teacher uploads”
- `templates/tupas/profile.html` — “Staff” badge should be `ADMIN` / `TEACHER` / `STUDENT`; Django admin link admin-only
- `templates/tupas/ai_generator.html` — `clear_existing` and copy currently keyed on `is_staff`

Use `effective_role(request.user)` in views / a context processor named `role` so templates can do `{% if role == 'ADMIN' %}`.

### 4. AI generator deck list ignores TEACHER
`tupas/forms.py` `AIReviewerForm.__init__` gives **all** decks only to `user.is_staff`. Teachers can `can_edit_deck` any deck but can only pick their own in the AI form.

**Fix:** use `is_teacher_or_admin(user)` (or `effective_role in {'ADMIN','TEACHER'}`) for the all-decks queryset. Keep `clear_existing` (destructive wipe) **admin-only** if that is the intended policy — document it.

### 5. Profile deck count uses `is_staff`
`tupas/views/profile.py`:
```python
deck_count = Deck.objects.filter(created_by=user).count() if not user.is_staff else Deck.objects.count()
```
Teachers who curate shared decks get the student count. Use the same role helper.

### 6. Last-admin lockout is incomplete
`tupas/views/users.py` `set_user_role` only blocks demoting a **superuser**. A non-superuser `is_staff` admin can uncheck **make_admin** on themselves (or the last staff account) and lose ADMIN.

**Fix:** before saving, if the target is currently ADMIN (`is_staff` or superuser) and the request would remove the last ADMIN, refuse with a message. Same guard if an admin demotes themselves.

Also add a confirm dialog on the role form (especially **make ADMIN** and removing ADMIN).

### 7. `user_list` filters roles in Python, not SQL
`tupas/views/users.py` builds `rows` for **every** user, then filters `role_filter` in the loop, then paginates. Fine for a classroom, wrong for production.

**Fix:** filter role in the queryset (`Q(is_staff=True) | Q(is_superuser=True)` for ADMIN; `profile__role='TEACHER'`; the rest STUDENT) before `Paginator`.

### 8. `security_check` does not audit the new RBAC
`tupas/management/commands/security_check.py` has no check for:
- `/users/` views still decorated with `teacher_or_admin_required` / `admin_required`
- `StudentProfile.role` field exists
- CSP / COOP / CORP actually set by middleware (it only greps `default-src` in settings)
- Custom 403 handler installed

Add these checks.

### 9. Documentation is out of date
`TUPAS_DOCUMENTATION.md` and `docs/plan.md` still describe only Student vs `is_admin` / Django admin.

Add a section covering:
- Three roles and what each can do
- `/users/` + role change (CSRF, admin-only writes)
- Teachers: read-only directory, can curate any deck, cannot change roles
- Security headers list (CSP, COOP, CORP, no-store, HSTS)
- Update the ERD: `StudentProfile.role` (not a boolean `is_admin` on users as the only source)
- Acceptance checklist items for RBAC + 403 page + teacher nav

### 10. Leftover patch script
Delete `_patch_tests.py` in the project root. It already appended `RBACTests` into `tupas/tests.py`.

### 11. Missing tests (add to `RBACTests` or a new class)
- Anonymous GET `/users/` redirects to login (not 200/403 HTML).
- Teacher **sees** the Users nav link; student does not.
- Custom 403 page renders for a student hitting `/users/`.
- `set_user_role` rejects `role=ADMIN` and junk values.
- Last non-superuser admin cannot be demoted.
- Security headers present on dashboard, login, and a 403 response (not only `/users/`).
- Signed-in pages send `Cache-Control: no-store`.
- AJAX `mark_progress` still requires CSRF.
- Teacher appears in `AIReviewerForm` deck queryset for decks they do not own.

### 12. Optional hardening (only if time remains)
- CSP still allows `'unsafe-inline'` for scripts/styles (templates rely on it). Prefer nonces if you can do it without breaking Bootstrap inline `onclick` / theme boot script. Do **not** add `unsafe-eval`.
- No audit log of role changes. A simple `LogEntry` or a small `RoleChange` model (actor, target, old→new, time) would help.
- `set_user_role` has no rate limit (admin-only; low risk).
- `SESSION_COOKIE_AGE` default is 14 days (`1209600`). Consider a shorter default for a student app or make it env-tunable in docs.

---

## Style / constraints for the next AI

- Django 5.2, SQLite dev DB, Bootstrap 5, function views.
- Do not add a new dependency for RBAC or dotenv.
- Keep CSRF on every POST. Do not disable `CsrfViewMiddleware`.
- Do not put ADMIN in `StudentProfile.role` — ADMIN stays `is_staff` / `is_superuser`.
- Superusers are never demoted from the in-app form.
- Study Buddy documents stay owner-only (staff do **not** get implicit access).
- Run: `venv\Scripts\python.exe manage.py test tupas` (Windows; use the venv, not system Python).
- Do not leave another `_patch_*.py` in the repo root.

## Acceptance

1. Teacher sees **Users**, opens the directory, cannot change roles.
2. Student hitting `/users/` or another person's deck edit gets a branded 403, not Django's default.
3. Nav / badges use ADMIN / TEACHER / STUDENT consistently.
4. Last admin cannot be demoted from the app.
5. `python manage.py security_check` includes RBAC checks and passes.
6. Full test suite still passes; no new migrations unless you add an audit model.
