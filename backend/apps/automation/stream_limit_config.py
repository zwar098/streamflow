"""
Stream Limit Config for StreamFlow.

Stores per-channel and per-group overrides for the "max streams assigned to a
channel" setting. This used to live only on the automation profile's
``stream_checking.stream_limit`` field; it is now configurable directly on a
channel or a channel group (mirroring the existing regex match-settings UX),
with the profile field kept as a fallback for anyone who already relies on it.

Resolution precedence: channel override -> group override -> profile's
``stream_checking.stream_limit`` -> 0 (unlimited).
"""

import threading
from typing import Any, Dict, Optional, Union

from apps.core.logging_config import setup_logging

logger = setup_logging(__name__)

CHANNEL_STREAM_LIMITS_KEY = 'channel_stream_limits'
GROUP_STREAM_LIMITS_KEY = 'group_stream_limits'


class StreamLimitConfig:
    """Manages per-channel and per-group stream limit overrides."""

    def __init__(self):
        self.lock = threading.RLock()
        self.channel_limits = self._load(CHANNEL_STREAM_LIMITS_KEY)
        self.group_limits = self._load(GROUP_STREAM_LIMITS_KEY)

    @staticmethod
    def _load(key: str) -> Dict[str, int]:
        from apps.database.manager import get_db_manager
        db = get_db_manager()
        data = db.get_system_setting(key, {}) or {}
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        for entry_id, value in data.items():
            limit = _coerce_limit(value)
            if limit is not None:
                cleaned[str(entry_id)] = limit
        return cleaned

    def _save(self, key: str, data: Dict[str, int]) -> bool:
        from apps.database.manager import get_db_manager
        db = get_db_manager()
        return db.set_system_setting(key, data)

    # ------------------------------------------------------------------
    # Channel-level overrides
    # ------------------------------------------------------------------

    def get_channel_stream_limit(self, channel_id: Union[str, int]) -> Optional[int]:
        """Return the channel's override, or None if no override is set."""
        with self.lock:
            return self.channel_limits.get(str(channel_id))

    def set_channel_stream_limit(self, channel_id: Union[str, int], stream_limit: int) -> None:
        limit = _coerce_limit(stream_limit)
        if limit is None:
            raise ValueError("stream_limit must be a non-negative integer")
        with self.lock:
            self.channel_limits[str(channel_id)] = limit
            self._save(CHANNEL_STREAM_LIMITS_KEY, self.channel_limits)

    def delete_channel_stream_limit(self, channel_id: Union[str, int]) -> None:
        with self.lock:
            if self.channel_limits.pop(str(channel_id), None) is not None:
                self._save(CHANNEL_STREAM_LIMITS_KEY, self.channel_limits)

    def bulk_set_channel_stream_limits(self, channel_ids, stream_limit: int) -> None:
        limit = _coerce_limit(stream_limit)
        if limit is None:
            raise ValueError("stream_limit must be a non-negative integer")
        with self.lock:
            for channel_id in channel_ids:
                self.channel_limits[str(channel_id)] = limit
            self._save(CHANNEL_STREAM_LIMITS_KEY, self.channel_limits)

    # ------------------------------------------------------------------
    # Group-level overrides
    # ------------------------------------------------------------------

    def get_group_stream_limit(self, group_id: Union[str, int]) -> Optional[int]:
        with self.lock:
            return self.group_limits.get(str(group_id))

    def set_group_stream_limit(self, group_id: Union[str, int], stream_limit: int) -> None:
        limit = _coerce_limit(stream_limit)
        if limit is None:
            raise ValueError("stream_limit must be a non-negative integer")
        with self.lock:
            self.group_limits[str(group_id)] = limit
            self._save(GROUP_STREAM_LIMITS_KEY, self.group_limits)

    def delete_group_stream_limit(self, group_id: Union[str, int]) -> None:
        with self.lock:
            if self.group_limits.pop(str(group_id), None) is not None:
                self._save(GROUP_STREAM_LIMITS_KEY, self.group_limits)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def get_effective_stream_limit(
        self,
        channel_id: Optional[Union[str, int]],
        group_id: Optional[Union[str, int]],
        profile_stream_limit: int = 0,
    ) -> int:
        """Resolve channel override -> group override -> profile field -> 0."""
        if channel_id is not None:
            channel_override = self.get_channel_stream_limit(channel_id)
            if channel_override is not None:
                return channel_override

        if group_id is not None:
            group_override = self.get_group_stream_limit(group_id)
            if group_override is not None:
                return group_override

        return _coerce_limit(profile_stream_limit) or 0


def _coerce_limit(value: Any) -> Optional[int]:
    """Return a non-negative int for a valid limit value, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get('stream_limit')
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if limit >= 0 else None


# Global instance and lock for thread-safe singleton
_stream_limit_config = None
_config_lock = threading.Lock()


def get_stream_limit_config() -> StreamLimitConfig:
    """Get the global StreamLimitConfig instance (thread-safe singleton)."""
    global _stream_limit_config
    if _stream_limit_config is None:
        with _config_lock:
            if _stream_limit_config is None:
                _stream_limit_config = StreamLimitConfig()
    return _stream_limit_config


def reset_stream_limit_config() -> None:
    """Reset the singleton (test helper)."""
    global _stream_limit_config
    with _config_lock:
        _stream_limit_config = None
