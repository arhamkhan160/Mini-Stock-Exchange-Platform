"""bcrypt hashing, kept separate from common.security (which only handles JWT).

Used directly, NOT via passlib — passlib 1.7.4 crashes against bcrypt >= 4.1,
per requirements-base.txt.
"""

import bcrypt

MAX_PASSWORD_BYTES = 72  # bcrypt silently truncates beyond this; reject instead


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
