"""Stream Limit API handler functions.

Handles per-channel and per-group overrides for the max number of streams
kept assigned to a channel. See ``apps.automation.stream_limit_config`` for
the storage/resolution logic (channel -> group -> profile fallback).
"""

from typing import Any, Callable

from flask import jsonify

from apps.api.schemas import BulkStreamLimitSchema, StreamLimitSchema
from apps.core.api_responses import error_response
from apps.core.exceptions import ValidationError
from apps.core.logging_config import setup_logging

logger = setup_logging(__name__)


def get_channel_stream_limit_response(*, channel_id: str, get_stream_limit_config: Callable[[], Any]):
    """Handle fetching a channel's stream limit override."""
    try:
        config = get_stream_limit_config()
        stream_limit = config.get_channel_stream_limit(channel_id)
        return jsonify({"channel_id": channel_id, "stream_limit": stream_limit})
    except Exception as exc:
        logger.error(f"Error getting stream limit for channel {channel_id}: {exc}")
        return jsonify({"error": "Internal Server Error"}), 500


def set_channel_stream_limit_response(
    *,
    channel_id: str,
    payload: Any,
    get_stream_limit_config: Callable[[], Any],
):
    """Handle setting a channel's stream limit override."""
    try:
        parsed = StreamLimitSchema.from_payload(payload)
        config = get_stream_limit_config()
        config.set_channel_stream_limit(channel_id, parsed.stream_limit)
        return jsonify({"message": "Stream limit updated successfully", "stream_limit": parsed.stream_limit})
    except ValidationError as exc:
        return error_response(exc.message, status_code=exc.status_code, code=exc.error_code, details=exc.details)
    except Exception as exc:
        logger.error(f"Error setting stream limit for channel {channel_id}: {exc}")
        return error_response("Internal Server Error", status_code=500, code="internal_error")


def delete_channel_stream_limit_response(*, channel_id: str, get_stream_limit_config: Callable[[], Any]):
    """Handle clearing a channel's stream limit override (falls back to group/profile)."""
    try:
        config = get_stream_limit_config()
        config.delete_channel_stream_limit(channel_id)
        return jsonify({"message": "Stream limit override cleared"})
    except Exception as exc:
        logger.error(f"Error clearing stream limit for channel {channel_id}: {exc}")
        return jsonify({"error": "Internal Server Error"}), 500


def get_group_stream_limit_response(*, group_id: int, get_stream_limit_config: Callable[[], Any]):
    """Handle fetching a group's default stream limit override."""
    try:
        config = get_stream_limit_config()
        stream_limit = config.get_group_stream_limit(group_id)
        return jsonify({"group_id": group_id, "stream_limit": stream_limit})
    except Exception as exc:
        logger.error(f"Error getting stream limit for group {group_id}: {exc}")
        return jsonify({"error": "Internal Server Error"}), 500


def set_group_stream_limit_response(
    *,
    group_id: int,
    payload: Any,
    get_stream_limit_config: Callable[[], Any],
):
    """Handle setting a group's default stream limit override."""
    try:
        parsed = StreamLimitSchema.from_payload(payload)
        config = get_stream_limit_config()
        config.set_group_stream_limit(group_id, parsed.stream_limit)
        return jsonify({"message": "Group stream limit updated successfully", "stream_limit": parsed.stream_limit})
    except ValidationError as exc:
        return error_response(exc.message, status_code=exc.status_code, code=exc.error_code, details=exc.details)
    except Exception as exc:
        logger.error(f"Error setting stream limit for group {group_id}: {exc}")
        return error_response("Internal Server Error", status_code=500, code="internal_error")


def delete_group_stream_limit_response(*, group_id: int, get_stream_limit_config: Callable[[], Any]):
    """Handle clearing a group's default stream limit override (falls back to profile)."""
    try:
        config = get_stream_limit_config()
        config.delete_group_stream_limit(group_id)
        return jsonify({"message": "Group stream limit override cleared"})
    except Exception as exc:
        logger.error(f"Error clearing stream limit for group {group_id}: {exc}")
        return jsonify({"error": "Internal Server Error"}), 500


def bulk_set_channel_stream_limits_response(*, payload: Any, get_stream_limit_config: Callable[[], Any]):
    """Handle setting the same stream limit override across multiple channels."""
    try:
        parsed = BulkStreamLimitSchema.from_payload(payload)
        config = get_stream_limit_config()
        config.bulk_set_channel_stream_limits(parsed.channel_ids, parsed.stream_limit)
        return jsonify(
            {
                "message": f"Updated stream limit for {len(parsed.channel_ids)} channel(s)",
                "success_count": len(parsed.channel_ids),
                "stream_limit": parsed.stream_limit,
            }
        )
    except ValidationError as exc:
        return error_response(exc.message, status_code=exc.status_code, code=exc.error_code, details=exc.details)
    except Exception as exc:
        logger.error(f"Error bulk setting channel stream limits: {exc}")
        return error_response("Internal Server Error", status_code=500, code="internal_error")
