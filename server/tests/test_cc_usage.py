from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from routes.cc_usage import _format_resets_at

client = TestClient(app)


# --- _format_resets_at unit tests ---

def test_format_resets_at_none():
    assert _format_resets_at(None) is None


def test_format_resets_at_hours_and_minutes():
    # +30s buffer absorbs the sub-second gap between strftime truncation and the second datetime.now() call
    dt = datetime.now(timezone.utc) + timedelta(hours=2, minutes=5, seconds=30)
    assert _format_resets_at(dt.strftime("%Y-%m-%dT%H:%M:%SZ")) == "2 hr 5 min"


def test_format_resets_at_only_hours():
    dt = datetime.now(timezone.utc) + timedelta(hours=3, seconds=30)
    assert _format_resets_at(dt.strftime("%Y-%m-%dT%H:%M:%SZ")) == "3 hr"


def test_format_resets_at_only_minutes():
    dt = datetime.now(timezone.utc) + timedelta(minutes=45, seconds=30)
    assert _format_resets_at(dt.strftime("%Y-%m-%dT%H:%M:%SZ")) == "45 min"


def test_format_resets_at_expired():
    dt = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert _format_resets_at(dt.strftime("%Y-%m-%dT%H:%M:%SZ")) == "0 min"


def test_format_resets_at_over_24h():
    dt = datetime.now(timezone.utc) + timedelta(hours=45, minutes=10)
    result = _format_resets_at(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    # Should be a weekday + time string, not "X hr Y min"
    assert result is not None
    assert "hr" not in result
    # Format: "Mon 6:00 PM" — day abbrev + time
    parts = result.split(" ")
    assert len(parts) == 3
    assert parts[0] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# --- /v1/cc-usage endpoint tests ---

def _make_mock_client(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = status_code < 400
    resp.json.return_value = body

    mock = AsyncMock()
    mock.__aenter__.return_value = mock
    mock.__aexit__.return_value = None
    mock.get.return_value = resp
    return mock


@patch("routes.cc_usage._get_token", return_value="fake-token")
@patch("routes.cc_usage.httpx.AsyncClient")
def test_cc_usage_happy_path(mock_client_cls, _mock_token):
    mock_client_cls.return_value = _make_mock_client(200, {
        "five_hour": {"utilization": 0.42, "resets_at": "2026-05-15T18:05:00Z"},
        "seven_day": {"utilization": 0.18, "resets_at": "2026-05-22T00:00:00Z"},
    })

    resp = client.get("/v1/cc-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["five_hour"]["utilization"] == pytest.approx(0.42)
    assert data["five_hour"]["resets_at"] is not None


@patch("routes.cc_usage._get_token", return_value="fake-token")
@patch("routes.cc_usage.httpx.AsyncClient")
def test_cc_usage_null_five_hour(mock_client_cls, _mock_token):
    mock_client_cls.return_value = _make_mock_client(200, {"five_hour": None})

    resp = client.get("/v1/cc-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["five_hour"]["utilization"] is None
    assert data["five_hour"]["resets_at"] is None


@patch("routes.cc_usage._get_token", side_effect=HTTPException(status_code=401, detail="Claude Code credentials not found"))
def test_cc_usage_keychain_failure(_mock_token):
    resp = client.get("/v1/cc-usage")
    assert resp.status_code == 401


@patch("routes.cc_usage._get_token", return_value="fake-token")
@patch("routes.cc_usage.httpx.AsyncClient")
def test_cc_usage_upstream_401(mock_client_cls, _mock_token):
    mock_client_cls.return_value = _make_mock_client(401, {})

    resp = client.get("/v1/cc-usage")
    assert resp.status_code == 401


@patch("routes.cc_usage._get_token", return_value="fake-token")
@patch("routes.cc_usage.httpx.AsyncClient")
def test_cc_usage_upstream_500(mock_client_cls, _mock_token):
    mock_client_cls.return_value = _make_mock_client(500, {})

    resp = client.get("/v1/cc-usage")
    assert resp.status_code == 502
