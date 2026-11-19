# TUPAS Admin account

Use this to log in as administrator.

## In-app login (`/login/`)

| Field | Value |
|---|---|
| Username | `admin` |
| Password | `TupasAdmin2026!` |
| Email | `admin@tupas.local` |
| Role | ADMIN (`is_staff` + `is_superuser`) |

After login you land on the **Admin dashboard** (accounts, activity log, stats).

## Django admin (`/admin/`)

Same username and password.

## Other accounts in the dev DB

| Username | Role | Notes |
|---|---|---|
| `admin` | ADMIN | Created 2026-11-19 |
| `juday` | STUDENT | Existing student (`juday@gmail.com`) |
| `ui_preview` | STUDENT | Test/preview account |
| `__browser_probe__` | — | Probe/test row |

Change the admin password on **My Profile & Settings** after first login. Do not commit this file with a real production password.
