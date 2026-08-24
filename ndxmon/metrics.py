"""指标计算。

口径约定（刻意写死，避免歧义）：
- 「较昨日涨跌幅」= (最新收盘 - 上一交易日收盘) / 上一交易日收盘，
  「上一交易日」取自实际返回的日线序列，因此周末与美股假日自动跳过。
- 「历史最高点」= 区间内最高「收盘价」，不使用盘中最高价，
  避免闪崩/插针导致回撤被高估。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .providers import Bar


class DataQualityError(ValueError):
    pass


@dataclass
class IndexReport:
    name: str
    source: str
    data_start: date
    last_day: date
    last_close: float
    prev_day: date
    prev_close: float
    day_change: float
    day_pct: float
    ath_close: float
    ath_day: date
    drawdown_pct: float
    trading_days_since_ath: int
    lookbacks: dict[int, float | None]

    @property
    def is_new_ath(self) -> bool:
        return self.last_day == self.ath_day


def build_report(
    name: str,
    bars: list[Bar],
    source: str,
    lookback_days: tuple[int, ...] = (5, 20, 60),
    sanity_max_day_pct: float = 20.0,
) -> IndexReport:
    if len(bars) < 2:
        raise DataQualityError(f"{name}: 日线数据不足（仅 {len(bars)} 根）")
    if any(b.close <= 0 for b in bars):
        raise DataQualityError(f"{name}: 存在非正收盘价，数据源可能有误")

    last, prev = bars[-1], bars[-2]
    day_pct = (last.close / prev.close - 1) * 100
    if abs(day_pct) > sanity_max_day_pct:
        raise DataQualityError(
            f"{name}: 单日涨跌幅 {day_pct:.2f}% 超出合理范围，判定为脏数据"
        )

    ath_bar = max(bars, key=lambda b: (b.close, b.day))
    lookbacks = {n: _pct_over(bars, n) for n in lookback_days}

    return IndexReport(
        name=name,
        source=source,
        data_start=bars[0].day,
        last_day=last.day,
        last_close=last.close,
        prev_day=prev.day,
        prev_close=prev.close,
        day_change=last.close - prev.close,
        day_pct=day_pct,
        ath_close=ath_bar.close,
        ath_day=ath_bar.day,
        drawdown_pct=(last.close / ath_bar.close - 1) * 100,
        trading_days_since_ath=sum(1 for b in bars if b.day > ath_bar.day),
        lookbacks=lookbacks,
    )


def _pct_over(bars: list[Bar], n_trading_days: int) -> float | None:
    """相对 n 个交易日之前收盘价的涨跌幅，历史不足时返回 None。"""
    if len(bars) <= n_trading_days:
        return None
    base = bars[-1 - n_trading_days].close
    return (bars[-1].close / base - 1) * 100
