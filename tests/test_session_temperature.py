"""Tests for _LiveSession._get_session_temperature() and session config constants."""

import time
from pathlib import Path
from unittest.mock import patch

# ── Config constants ──


def test_session_warm_threshold_default():
    """SESSION_WARM_THRESHOLD 默认 3600 秒（60 分钟 × 60，对齐 prompt cache TTL 1h）。"""
    from brain.config import SESSION_WARM_THRESHOLD

    assert SESSION_WARM_THRESHOLD == 3600


def test_session_reset_threshold_default():
    """SESSION_RESET_THRESHOLD 默认 14400 秒（4 小时 × 3600）。"""
    from brain.config import SESSION_RESET_THRESHOLD

    assert SESSION_RESET_THRESHOLD == 14400


def test_session_context_soft_threshold_default():
    """SESSION_CONTEXT_SOFT_THRESHOLD 默认 160000。"""
    from brain.config import SESSION_CONTEXT_SOFT_THRESHOLD

    assert SESSION_CONTEXT_SOFT_THRESHOLD == 160000


def test_session_context_hard_threshold_default():
    """SESSION_CONTEXT_HARD_THRESHOLD 默认 200000。"""
    from brain.config import SESSION_CONTEXT_HARD_THRESHOLD

    assert SESSION_CONTEXT_HARD_THRESHOLD == 200000


# ── _get_session_temperature ──


def _make_session(**kwargs):
    """创建一个 _LiveSession 实例用于测试（不连接 CC）。"""
    from brain.executor.cc import _LiveSession

    return _LiveSession(
        channel_id="test-channel",
        cwd=Path("/tmp"),
        **kwargs,
    )


class TestGetSessionTemperature:
    """_get_session_temperature() 三种温度判断 + 边界值。"""

    def test_cold_when_never_active(self):
        """last_activity == 0（从未活动）→ cold。"""
        session = _make_session()
        assert session.last_activity == 0
        assert session._get_session_temperature() == "cold"

    def test_hot_when_just_active(self):
        """刚活动（1 秒前）→ hot。"""
        session = _make_session()
        session.last_activity = time.time() - 1
        assert session._get_session_temperature() == "hot"

    def test_hot_at_30_minutes(self):
        """30 分钟前活动（< 60 分钟阈值）→ hot。"""
        session = _make_session()
        session.last_activity = time.time() - 1800
        assert session._get_session_temperature() == "hot"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 3600)
    def test_warm_at_boundary(self):
        """恰好等于 warm_threshold（3600 秒）→ warm。"""
        session = _make_session()
        session.last_activity = time.time() - 3600
        assert session._get_session_temperature() == "warm"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 3600)
    def test_warm_at_2_hours(self):
        """2 小时前（warm 区间中段）→ warm。"""
        session = _make_session()
        session.last_activity = time.time() - 7200
        assert session._get_session_temperature() == "warm"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 3600)
    @patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 14400)
    def test_warm_just_below_reset(self):
        """reset_threshold - 1 秒 → warm。"""
        session = _make_session()
        session.last_activity = time.time() - 14399
        assert session._get_session_temperature() == "warm"

    @patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 14400)
    def test_cold_at_boundary(self):
        """恰好等于 reset_threshold（14400 秒）→ cold。"""
        session = _make_session()
        session.last_activity = time.time() - 14400
        assert session._get_session_temperature() == "cold"

    @patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 14400)
    def test_cold_at_24_hours(self):
        """24 小时前 → cold。"""
        session = _make_session()
        session.last_activity = time.time() - 86400
        assert session._get_session_temperature() == "cold"

    def test_hot_boundary_just_below_warm(self):
        """warm_threshold - 1 秒 → hot。"""
        from brain.config import SESSION_WARM_THRESHOLD

        session = _make_session()
        session.last_activity = time.time() - (SESSION_WARM_THRESHOLD - 1)
        assert session._get_session_temperature() == "hot"
