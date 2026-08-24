from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .metrics import IndexReport

# 中文习惯：涨红跌绿
_UP, _DOWN, _FLAT = "#d94040", "#1a9e5c", "#6b7280"

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _color(pct: float | None) -> str:
    if pct is None or abs(pct) < 0.005:
        return _FLAT
    return _UP if pct > 0 else _DOWN


def _signed(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}"


def render_html(
    reports: list[IndexReport], trade_day: str, generated_at: datetime
) -> str:
    sources = "、".join(sorted({r.source for r in reports}))
    return _env.get_template("email.html.j2").render(
        reports=reports,
        trade_day=trade_day,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M"),
        sources=sources,
        color=_color,
        signed=_signed,
    )


def render_subject(prefix: str, reports: list[IndexReport], trade_day: str) -> str:
    """主题行直接带上核心数字，手机通知栏不点开也能看到。"""
    parts = [f"{r.name} {r.day_pct:+.2f}%" for r in reports]
    return f"{prefix} {trade_day} " + " | ".join(parts)


def render_text(reports: list[IndexReport], trade_day: str) -> str:
    """纯文本兜底，供不渲染 HTML 的客户端使用。"""
    lines = [f"美股指数日报 {trade_day}（美东收盘）", ""]
    for r in reports:
        lines.append(f"{r.name}  {r.last_close:,.2f}")
        lines.append(
            f"  较昨收 {r.day_change:+,.2f} ({r.day_pct:+.2f}%)，"
            f"昨收 {r.prev_close:,.2f} @ {r.prev_day}"
        )
        if r.is_new_ath:
            lines.append("  创历史新高收盘价")
        else:
            lines.append(
                f"  距历史最高收盘价 {r.ath_close:,.2f} @ {r.ath_day} "
                f"回撤 {r.drawdown_pct:+.2f}%，已 {r.trading_days_since_ath} 个交易日未创新高"
            )
        lines.append("")
    return "\n".join(lines)
