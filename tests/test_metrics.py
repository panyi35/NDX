from datetime import date

import pytest

from ndxmon.metrics import DataQualityError, build_report
from ndxmon.providers import Bar


def bar(day: str, close: float) -> Bar:
    d = date.fromisoformat(day)
    return Bar(day=d, open=close, high=close, low=close, close=close)


def test_prev_close_skips_weekend_and_holidays():
    # 序列中不存在的日期（周末、假日）不应参与「昨日」的判定
    bars = [bar("2026-01-16", 100.0), bar("2026-01-20", 110.0)]
    r = build_report("X", bars, "test", lookback_days=())
    assert r.prev_day == date(2026, 1, 16)
    assert r.day_pct == pytest.approx(10.0)


def test_ath_uses_closing_price_not_intraday_spike():
    bars = [
        bar("2026-01-05", 100.0),
        Bar(day=date(2026, 1, 6), open=100, high=200, low=99, close=105),
        bar("2026-01-07", 90.0),
    ]
    r = build_report("X", bars, "test", lookback_days=())
    assert r.ath_close == 105.0
    assert r.ath_day == date(2026, 1, 6)
    assert r.drawdown_pct == pytest.approx((90 / 105 - 1) * 100)
    assert r.trading_days_since_ath == 1
    assert not r.is_new_ath


def test_new_high_reports_zero_drawdown():
    bars = [bar("2026-01-05", 100.0), bar("2026-01-06", 120.0)]
    r = build_report("X", bars, "test", lookback_days=())
    assert r.is_new_ath
    assert r.drawdown_pct == pytest.approx(0.0)


def test_lookback_returns_none_when_history_is_short():
    bars = [bar("2026-01-05", 100.0), bar("2026-01-06", 101.0)]
    r = build_report("X", bars, "test", lookback_days=(5,))
    assert r.lookbacks[5] is None


def test_absurd_move_is_rejected_as_dirty_data():
    bars = [bar("2026-01-05", 100.0), bar("2026-01-06", 500.0)]
    with pytest.raises(DataQualityError):
        build_report("X", bars, "test", lookback_days=(), sanity_max_day_pct=20.0)


def test_non_positive_close_is_rejected():
    bars = [bar("2026-01-05", 100.0), bar("2026-01-06", 0.0)]
    with pytest.raises(DataQualityError):
        build_report("X", bars, "test", lookback_days=())
