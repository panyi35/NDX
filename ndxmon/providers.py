"""日线历史数据源。

主源为腾讯行情（国内直连可用），失败时回落到 Yahoo Finance
（GitHub Actions 的美国 runner 上可用，国内直连通常返回 403）。
两个源都只取日线收盘价，指数无需复权。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

# 腾讯单次请求最多返回 800 根日线，需要按结束日期向前翻页。
_TENCENT_PAGE = 800
_TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class DataSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


def fetch_history(code: str, yahoo_symbol: str, start: date) -> tuple[list[Bar], str]:
    """返回 (按日期升序的日线, 实际使用的数据源名)。"""
    errors = []
    for name, fn in (("tencent", _fetch_tencent), ("yahoo", _fetch_yahoo)):
        symbol = code if name == "tencent" else yahoo_symbol
        try:
            bars = fn(symbol, start)
            if bars:
                return bars, name
            errors.append(f"{name}: 返回空数据")
        except Exception as exc:  # noqa: BLE001 - 任一源失败都应尝试下一个
            log.warning("数据源 %s 获取 %s 失败: %s", name, symbol, exc)
            errors.append(f"{name}: {exc}")
    raise DataSourceError(f"所有数据源均失败 ({code}) -> " + "; ".join(errors))


def _get_json(url: str, params: dict | None = None, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url, params=params, headers={"User-Agent": _UA}, timeout=20
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise DataSourceError(str(last))


def _fetch_tencent(code: str, start: date) -> list[Bar]:
    collected: dict[date, Bar] = {}
    end = ""  # 空字符串表示「截至最新交易日」
    for _ in range(40):  # 上限保护，40 页足以覆盖 2000 年至今
        payload = _get_json(
            _TENCENT_URL, {"param": f"{code},day,,{end},{_TENCENT_PAGE},qfq"}
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DataSourceError(f"接口返回异常: {payload.get('msg') or payload}")
        node = data.get(code) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            break

        page = [_parse_tencent_row(r) for r in rows]
        page = [b for b in page if b is not None]
        if not page:
            break

        new = {b.day: b for b in page if b.day not in collected}
        collected.update(new)

        oldest = min(b.day for b in page)
        if oldest <= start or not new:
            break
        end = (oldest - timedelta(days=1)).isoformat()

    bars = [b for d, b in sorted(collected.items()) if d >= start]
    return bars


def _parse_tencent_row(row: list) -> Bar | None:
    # 行格式: [日期, 开, 收, 高, 低, 成交量, ...]
    try:
        return Bar(
            day=date.fromisoformat(row[0]),
            open=float(row[1]),
            close=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
        )
    except (ValueError, IndexError, TypeError):
        return None


def _fetch_yahoo(symbol: str, start: date) -> list[Bar]:
    payload = _get_json(
        _YAHOO_URL.format(symbol=symbol),
        {"range": "max", "interval": "1d", "includePrePost": "false"},
    )
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise DataSourceError(f"接口返回异常: {payload}")
    node = result[0]
    stamps = node.get("timestamp") or []
    quote = (node.get("indicators") or {}).get("quote") or [{}]
    q = quote[0]

    bars = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (q.get(k, [])[i] for k in ("open", "high", "low", "close"))
        if c is None:
            continue  # 尚未收盘或数据缺失的交易日
        day = _yahoo_trading_day(ts, node)
        if day < start:
            continue
        bars.append(
            Bar(day=day, open=o or c, high=h or c, low=l or c, close=float(c))
        )
    bars.sort(key=lambda b: b.day)
    return bars


def _yahoo_trading_day(ts: int, node: dict) -> date:
    """Yahoo 的时间戳是 UTC 秒，需叠加交易所偏移才能还原出正确的交易日。"""
    offset = ((node.get("meta") or {}).get("gmtoffset")) or 0
    return datetime.fromtimestamp(ts + offset, tz=timezone.utc).date()
