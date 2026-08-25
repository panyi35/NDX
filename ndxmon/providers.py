"""日线历史数据源。

主源为腾讯行情，备源为东方财富，两者在国内直连和 GitHub 的美国 runner 上都可用。

不使用 Yahoo Finance：它现在要求 cookie + crumb 握手，裸 GET 会返回 403/429，
作为兜底等于没有兜底。
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# 腾讯单次请求最多返回 800 根日线，需要按结束日期向前翻页。
_TENCENT_PAGE = 800
_TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"

# 东方财富单次即可返回全量历史，但限流较严：短时间内密集请求会被按 IP 拒绝，
# 表现为返回空响应（连接被直接关闭），且封禁会持续数分钟。因此每次请求前留出间隔。
_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_GAP_SECONDS = 3.0
_last_eastmoney_call = 0.0


class DataSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


def fetch_history(
    tencent_code: str, eastmoney_secid: str, start: date
) -> tuple[list[Bar], str]:
    """返回 (按日期升序的日线, 实际使用的数据源名)。"""
    attempts = [("tencent", _fetch_tencent, tencent_code)]
    if eastmoney_secid:
        attempts.append(("eastmoney", _fetch_eastmoney, eastmoney_secid))

    errors = []
    for name, fn, symbol in attempts:
        try:
            bars = fn(symbol, start)
            if bars:
                return bars, name
            errors.append(f"{name}: 返回空数据")
        except Exception as exc:  # noqa: BLE001 - 任一源失败都应尝试下一个
            log.warning("数据源 %s 获取 %s 失败: %s", name, symbol, exc)
            errors.append(f"{name}: {exc}")
    raise DataSourceError(
        f"所有数据源均失败 ({tencent_code}) -> " + "; ".join(errors)
    )


def _get_json(url: str, params: dict, retries: int = 4) -> dict:
    """带指数退避的 GET。限流表现为空响应或连接中断，因此需要重试。"""
    last = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2**attempt + random.uniform(0, 0.5))
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
        page = [b for b in (_parse_ohlc(r) for r in rows) if b is not None]
        if not page:
            break

        new = {b.day: b for b in page if b.day not in collected}
        collected.update(new)

        oldest = min(b.day for b in page)
        if oldest <= start or not new:
            break
        end = (oldest - timedelta(days=1)).isoformat()

    return [b for d, b in sorted(collected.items()) if d >= start]


def _fetch_eastmoney(secid: str, start: date) -> list[Bar]:
    global _last_eastmoney_call
    wait = _EASTMONEY_GAP_SECONDS - (time.monotonic() - _last_eastmoney_call)
    if wait > 0:
        time.sleep(wait)
    _last_eastmoney_call = time.monotonic()

    payload = _get_json(
        _EASTMONEY_URL,
        {
            "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": "101",  # 日线
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
        },
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataSourceError(f"接口返回异常: {payload}")

    rows = [line.split(",") for line in (data.get("klines") or [])]
    bars = [b for b in (_parse_ohlc(r) for r in rows) if b is not None]
    bars.sort(key=lambda b: b.day)
    return [b for b in bars if b.day >= start]


def _parse_ohlc(row: list) -> Bar | None:
    """腾讯与东方财富的行结构一致: [日期, 开, 收, 高, 低, 成交量, ...]。

    注意第 2、3 位是「收、高」而非直觉上的「高、低」，弄反会导致
    历史最高点被算成最高收盘价之上的值。
    """
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
