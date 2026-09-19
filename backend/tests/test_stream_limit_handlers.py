"""Tests for the Stream Limit API handlers (channel/group overrides)."""

from flask import Flask

from apps.api.stream_limit_handlers import (
    bulk_set_channel_stream_limits_response,
    delete_channel_stream_limit_response,
    delete_group_stream_limit_response,
    get_channel_stream_limit_response,
    get_group_stream_limit_response,
    set_channel_stream_limit_response,
    set_group_stream_limit_response,
)
from apps.automation.stream_limit_config import StreamLimitConfig

app = Flask(__name__)


def test_get_channel_stream_limit_defaults_to_none():
    config = StreamLimitConfig()
    with app.app_context():
        response = get_channel_stream_limit_response(
            channel_id="42", get_stream_limit_config=lambda: config
        )
    data = response.get_json()
    assert data == {"channel_id": "42", "stream_limit": None}


def test_set_and_get_channel_stream_limit():
    config = StreamLimitConfig()
    with app.app_context():
        set_response = set_channel_stream_limit_response(
            channel_id="42", payload={"stream_limit": 6}, get_stream_limit_config=lambda: config
        )
        get_response = get_channel_stream_limit_response(
            channel_id="42", get_stream_limit_config=lambda: config
        )

    assert set_response.get_json()["stream_limit"] == 6
    assert get_response.get_json()["stream_limit"] == 6


def test_set_channel_stream_limit_rejects_negative():
    config = StreamLimitConfig()
    with app.app_context():
        response, status = set_channel_stream_limit_response(
            channel_id="42", payload={"stream_limit": -3}, get_stream_limit_config=lambda: config
        )
    assert status == 400


def test_delete_channel_stream_limit_clears_override():
    config = StreamLimitConfig()
    config.set_channel_stream_limit("42", 6)
    with app.app_context():
        delete_channel_stream_limit_response(channel_id="42", get_stream_limit_config=lambda: config)
        get_response = get_channel_stream_limit_response(
            channel_id="42", get_stream_limit_config=lambda: config
        )
    assert get_response.get_json()["stream_limit"] is None


def test_set_and_delete_group_stream_limit():
    config = StreamLimitConfig()
    with app.app_context():
        set_group_stream_limit_response(
            group_id=7, payload={"stream_limit": 4}, get_stream_limit_config=lambda: config
        )
        get_response = get_group_stream_limit_response(group_id=7, get_stream_limit_config=lambda: config)
        assert get_response.get_json()["stream_limit"] == 4

        delete_group_stream_limit_response(group_id=7, get_stream_limit_config=lambda: config)
        get_response_after_delete = get_group_stream_limit_response(
            group_id=7, get_stream_limit_config=lambda: config
        )
        assert get_response_after_delete.get_json()["stream_limit"] is None


def test_bulk_set_channel_stream_limits():
    config = StreamLimitConfig()
    with app.app_context():
        response = bulk_set_channel_stream_limits_response(
            payload={"channel_ids": [1, 2, 3], "stream_limit": 5},
            get_stream_limit_config=lambda: config,
        )
    data = response.get_json()
    assert data["success_count"] == 3
    assert config.get_channel_stream_limit(1) == 5
    assert config.get_channel_stream_limit(2) == 5
    assert config.get_channel_stream_limit(3) == 5


def test_bulk_set_channel_stream_limits_requires_channel_ids():
    config = StreamLimitConfig()
    with app.app_context():
        response, status = bulk_set_channel_stream_limits_response(
            payload={"stream_limit": 5}, get_stream_limit_config=lambda: config
        )
    assert status == 400
