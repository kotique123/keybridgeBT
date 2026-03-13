"""
Sliding-window rate limiter for keyboard events.

Rejects bursts exceeding a configurable threshold (default: 20 key events/second).
Only applies to keyboard events — pointer events are exempt.

See docs/ARCHITECTURE.md §5.9 and docs/TASKS.md Task 14.
"""

import time
from collections import deque


class RateLimiter:
    """Sliding-window rate limiter."""

    def __init__(self, max_events: int = 20, window_seconds: float = 1.0):
        self._max_events = max_events
        self._window = window_seconds
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        """
        Record an event and return whether it should be allowed.
        Uses time.monotonic() to avoid clock-skew issues.
        """
        now = time.monotonic()

        # Evict expired timestamps
        while self._timestamps and self._timestamps[0] <= now - self._window:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._max_events:
            return False

        self._timestamps.append(now)
        return True

    def reset(self):
        """Clear state (call on reconnect)."""
        self._timestamps.clear()
