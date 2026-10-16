"""Explicit owner activity across async calls and physical worker futures."""

from __future__ import annotations

import functools
import threading
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _Activity:
    holders: int = 0
    used: int = 0
    discarded: bool = False


class OwnerActivity:
    """Counts leases, not jobs inferred from diagnostic snapshots.

    Future callbacks release on worker threads, including after loop shutdown.
    All admission and alias publication still belong to the event-loop app lock.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._owners: dict[str, _Activity] = {}
        self._aliases: dict[str, str] = {}
        self._clock = 0

    def register(self, owner: str) -> None:
        with self._lock:
            if owner not in self._owners:
                self._clock += 1
                self._owners[owner] = _Activity(used=self._clock)
            self._owners[owner].discarded = False
            self._aliases[owner] = owner

    def alias(self, path: str, owner: str) -> None:
        with self._lock:
            self.register(owner)
            self._aliases[path] = owner

    def touch(self, owner: str) -> None:
        with self._lock:
            self.register(owner)
            self._clock += 1
            self._owners[owner].used = self._clock

    def acquire(self, path: str):
        """Return an idempotent release, or None for work without a loaded owner."""
        with self._lock:
            owner = self._aliases.get(path)
            if owner is None:
                return None
            entry = self._owners[owner]
            entry.holders += 1
        released = False

        def release():
            nonlocal released
            with self._lock:
                if not released:
                    released = True
                    entry.holders -= 1
                    self._clock += 1
                    entry.used = self._clock
                    if entry.holders == 0 and entry.discarded:
                        self.forget(owner)

        return release

    def busy(self, owner: str) -> bool:
        with self._lock:
            entry = self._owners.get(owner)
            return entry is not None and entry.holders > 0

    def oldest(self, candidates) -> str | None:
        with self._lock:
            idle = [key for key in candidates if not self.busy(key)]
            return min(idle, key=lambda key: self._owners[key].used) if idle else None

    def forget(self, owner: str) -> None:
        with self._lock:
            if self.busy(owner):
                raise RuntimeError("cannot release an active store")
            self._owners.pop(owner, None)
            self._aliases = {path: key for path, key in self._aliases.items() if key != owner}

    def discard(self, owner: str) -> None:
        """A failed assembly leaves no store to retain after its waiters detach."""
        with self._lock:
            entry = self._owners.get(owner)
            if entry is not None:
                entry.discarded = True
                if not entry.holders:
                    self.forget(owner)


class _CallActivity:
    def __init__(self):
        self.releases = {}
        self.closed = False

    def retain(self, activity: OwnerActivity, owner: str) -> None:
        key = (activity, owner)
        if not self.closed and key not in self.releases:
            release = activity.acquire(owner)
            if release is not None:
                self.releases[key] = release

    def close(self) -> None:
        self.closed = True
        for release in self.releases.values():
            release()
        self.releases.clear()


_call_activity: ContextVar[_CallActivity | None] = ContextVar("owner_activity", default=None)


def retain_for_call(activity: OwnerActivity, owner: str) -> None:
    activity.touch(owner)
    scope = _call_activity.get()
    if scope is not None:
        scope.retain(activity, owner)


def scoped_activity(func):
    """Each call/task gets its own scope, never an inherited mutable lease set."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        scope = _CallActivity()
        token = _call_activity.set(scope)
        try:
            return await func(*args, **kwargs)
        finally:
            scope.close()
            _call_activity.reset(token)

    return wrapper
