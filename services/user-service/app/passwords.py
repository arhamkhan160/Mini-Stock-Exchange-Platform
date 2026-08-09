"""bcrypt hashing, kept separate from common.security (which only handles JWT).

Used directly, NOT via passlib — passlib 1.7.4 crashes against bcrypt >= 4.1,
per requirements-base.txt.
"""

import bcrypt

MAX_PASSWORD_BYTES = 72  # bcrypt silently truncates beyond this; reject instead of truncating


def hash_password(password: str) -> str:
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        # Belt and suspenders: UserCreate already rejects this, but hash_password
        # must never let bcrypt silently truncate an over-length password —
        # that would make two different passwords hash identically.
        raise ValueError(f"password too long (max {MAX_PASSWORD_BYTES} bytes)")
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:MAX_PASSWORD_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        return False  # a corrupt hash in the DB must not 500


# A fixed, valid bcrypt hash of a password nobody will ever type. Used to burn
# a constant amount of time when the identifier doesn't match any user, so
# "wrong password" and "unknown user" take the same wall-clock time and login
# can't be used to enumerate registered accounts.
_DUMMY_HASH = bcrypt.hashpw(b"no-such-account-timing-guard", bcrypt.gensalt(rounds=12)).decode("utf-8")


def burn_verify_time() -> None:
    verify_password("irrelevant", _DUMMY_HASH)
