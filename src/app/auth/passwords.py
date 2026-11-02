"""Password hashing — Argon2id via `argon2-cffi`.

Only the primitive lives here. Session and user policy are in `service.py`.

Why Argon2id rather than bcrypt/passlib: Argon2id is the current password
hashing recommendation (OWASP), `argon2-cffi` ships Windows wheels and is
actively maintained, and `passlib` has not released since 2020. The library
does the cryptography; no custom KDF is written in this project.
"""

from __future__ import annotations

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: OWASP-recommended first pass (19 MiB, 2 iterations) raised to 64 MiB / 3
#: iterations for a single-operator server. ~50-100 ms per verification on the
#: production host; acceptable for a login form and deliberately expensive for
#: an attacker with a copy of the database.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

_dummy_lock = threading.Lock()
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, str | None]:
    """Return (ok, replacement_hash).

    `replacement_hash` is set when the stored hash uses older parameters and
    should be upgraded after a successful login. A malformed or unknown hash is
    a failed verification, never an exception that could leak into a 500.
    """
    try:
        _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    except Exception:
        return False, None
    replacement = None
    try:
        if _HASHER.check_needs_rehash(password_hash):
            replacement = _HASHER.hash(password)
    except Exception:
        replacement = None
    return True, replacement


def dummy_hash() -> str:
    """A real hash used to equalise timing for an unknown username.

    Computed once, lazily: importing the app must not pay 100 ms of Argon2.
    """
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = _HASHER.hash("not-a-real-password")
    return _dummy_hash
