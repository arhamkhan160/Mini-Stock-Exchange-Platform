# User Service — service notes

**Port 8001 · database `user_db` · owner: Arham Apon Utsho (Team B)**

Identity: registration, login, profile, password change, and the internal
lookups other services use to resolve a user id to an email. No Redis, no
broker — Postgres is its only dependency, and `/ready` checks exactly that.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | public | 201, returns a token + the new profile |
| POST | `/auth/login` | public | OAuth2 form (`username` field accepts a username or an email, any case) |
| POST | `/auth/login-json` | public | same semantics, JSON `{email_or_username, password}` |
| POST | `/auth/change-password` | JWT | `{current_password, new_password}` |
| GET | `/users/me` | JWT | 404 if the token's account is gone |
| PATCH | `/users/me` | JWT | `{full_name?, username?}`; 401 if the account is gone or disabled |
| GET | `/internal/users/{id}` | `X-Internal-Key` | used by Notification to resolve an email |
| GET | `/internal/users?ids=a,b,c` | `X-Internal-Key` | batch, max 100 ids, malformed ids are skipped not rejected |
| GET | `/health` | public | liveness only, no DB call |
| GET | `/ready` | public | checks Postgres, 503 if down |

Swagger: `http://localhost:8001/docs`. Gateway strips `/api`, so the public
routes are `/api/auth/register` etc. — this service never sees the `/api` prefix.

Bad request bodies return **400** with `{"detail": "<message>"}`, not
FastAPI's default 422 — see `main.py`'s `RequestValidationError` handler,
which collapses pydantic's structured error list to the shared contract's
`{"detail": "message"}` shape (§1.6: "400 validation" is meant literally).

## Validation

- Email: RFC syntax via `EmailStr`, stored and compared lowercase.
- Username: `^[a-zA-Z0-9_]{3,30}$`, **unique case-insensitively** ("Ada" and
  "ada" collide) via a functional index on `lower(username)`, not a plain
  unique constraint.
- Password: 8–72 bytes, at least one letter and one digit. 72 bytes is
  bcrypt's own limit — `passwords.hash_password` raises rather than let
  bcrypt silently truncate a longer password into a different hash than the
  user expects.
- `full_name`: whitespace-only input is stored as `NULL`, not `""`.

## Edge cases handled

1. **Duplicate email/username, including a case-only difference** — there is
   no pre-check-then-insert race window. The unique index on `email` and the
   functional unique index on `lower(username)` are the single source of
   truth; two concurrent registrations for the same identity always produce
   exactly one 201 and one clean 409, never a 500 from a raw constraint
   violation.
2. **Login enumeration resistance** — an unknown identifier and a wrong
   password return the *identical* 401 body. An unknown identifier also runs
   a bcrypt comparison against a fixed dummy hash (`passwords.burn_verify_time`)
   before returning, so the two cases take comparable wall-clock time and
   response timing can't be used to probe which emails are registered.
3. **Disabled accounts** — `is_active=false` only ever surfaces as `403
   "account disabled"`, and only *after* the password has already verified —
   checking `is_active` before the password would let an attacker learn an
   account is disabled without knowing its password. An already-issued JWT
   for a disabled account gets a generic `401` on `PATCH /users/me` /
   `change-password` instead (see next point), not the specific 403 — that
   message is reserved for the login flow.
4. **Token for a deleted/disabled user** — `get_current_user` only decodes
   the JWT (no DB hit, by design, shared with every other service). Endpoints
   that *mutate* the user (`PATCH /users/me`, `change-password`) re-load the
   row and return 401 if it is missing or inactive, forcing a fresh login
   rather than confirming which of the two is true. `GET /users/me` is
   read-only and keeps its own 404-if-missing behavior.
5. **Username-change collision** goes through the same `IntegrityError` → 409
   path as registration — no separate pre-check to get out of sync with the
   real constraint.
6. **`sub` is always a string** — `create_access_token` (in `common.security`,
   shared) does `str(user_id)`; PyJWT ≥ 2.10 rejects a non-string subject.
7. **`/internal/users*` never leaks past the gateway** — it refuses to proxy
   any path containing `/internal/`, and both routes also require
   `X-Internal-Key`, so they are reachable only from inside the Docker network.
8. **Malformed ids on the internal batch lookup are skipped, not rejected** —
   a single bad id in a batch of 100 must not fail the whole call; more than
   100 ids *is* rejected (400), since that is a caller bug, not bad data.

## Known limitations

- No password reset / email verification flow — out of scope for the demo.
- `email` is immutable via the API (it's baked into every live JWT as a
  claim); there is no endpoint to change it.

## Running it alone

```bash
docker compose -f docker-compose.dev.yml up -d
```

```powershell
$env:PYTHONPATH=".;../../libs"
$env:DATABASE_URL="postgresql+asyncpg://mse:mse_pw@localhost:5433/user_db"
$env:SERVICE_PORT="8001"
$env:SERVICE_NAME="user-service"
python selfcheck.py
uvicorn app.main:app --reload --port 8001
```
