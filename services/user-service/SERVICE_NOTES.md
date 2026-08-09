# User Service — service notes

**Port 8001 · database `user_db` · owner: Arham Apon Utsho (Team B)**

Identity: registration, login, profile, and the internal lookup other services
use to resolve a user id to an email. No Redis, no broker — Postgres is its
only dependency, and `/ready` checks exactly that (no more, no less).

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | public | 201, returns a token + the new profile |
| POST | `/auth/login` | public | OAuth2 form (`username` field accepts a username or an email) |
| POST | `/auth/login-json` | public | same semantics, JSON body |
| GET | `/users/me` | JWT | |
| PATCH | `/users/me` | JWT | only `full_name` is mutable |
| GET | `/internal/users/{id}` | `X-Internal-Key` | used by Notification to resolve an email |
| GET | `/health` | public | liveness only, no DB call |
| GET | `/ready` | public | checks Postgres, 503 if down |

Swagger: `http://localhost:8001/docs`. Gateway strips `/api`, so the public
routes are `/api/auth/register` etc. — this service never sees the `/api` prefix.

## Edge cases handled

1. **Duplicate email/username** — there is no pre-check-then-insert race
   window. The unique index on `email`/`username` is the single source of
   truth; a `POST /auth/register` racing another with the same email always
   gets a clean 409, never a 500 from a constraint violation.
2. **Login by username or email** — the OAuth2 spec's `username` field is
   matched against both columns, so the frontend's single "Username or Email"
   input works without knowing which one the user typed.
3. **Generic 401 on bad credentials** — the same message is returned whether
   the identifier or the password was wrong, so login can't be used to
   enumerate registered emails.
4. **Email/username are immutable via `PATCH /users/me`** — a JWT bakes in
   `email` and `username` as claims; changing either without forcing
   re-login would desync every already-issued token. Only `full_name` is
   editable. Documented limitation, not an oversight.
5. **`/internal/users/{id}` never leaks past the gateway** — the gateway
   refuses to proxy any path containing `/internal/`, and this route also
   requires `X-Internal-Key`, so it is reachable only from inside the Docker
   network.
6. **Bad UUID / unknown id on the internal lookup** both return 404, not a
   500 — a malformed id is not a server error.
7. **Password length is bounded** at 72 bytes (bcrypt's own limit) so a huge
   payload can't be silently truncated into a different hash than the user
   expects.

## Known limitations

- No password reset / email verification flow — out of scope for the demo.
- `updated_at` exists on the model but nothing besides `PATCH /users/me`
  changes a row after creation.

## Running it alone

```bash
docker compose up -d postgres-user
```

```powershell
$env:PYTHONPATH=".;../../libs"
$env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5433/user_db"
$env:SERVICE_PORT="8001"
$env:SERVICE_NAME="user-service"
python selfcheck.py
uvicorn app.main:app --reload --port 8001
```
