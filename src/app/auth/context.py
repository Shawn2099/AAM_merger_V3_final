"""Request-scoped audit identity.

`audit_log.source` is free text. With authentication on, an operator action is
recorded as `operator:<username>`; with authentication off (tests, a lab
install) it stays the historical `"operator"`, so existing records and
assertions keep their meaning. Machine writers keep `"system"`/`"sync"`.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_DEFAULT_ACTOR = "operator"
_actor: ContextVar[str] = ContextVar("aam_audit_actor", default=_DEFAULT_ACTOR)


def set_actor(username: str) -> Token[str]:
    return _actor.set(f"operator:{username}")


def reset_actor(token: Token[str]) -> None:
    _actor.reset(token)


def actor_source() -> str:
    return _actor.get()
