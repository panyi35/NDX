from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import mailer, render
from .metrics import build_report
from .providers import fetch_history

ET = ZoneInfo("America/New_York")
CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent.parent

log = logging.getLogger("ndxmon")


def load_dotenv(path: Path) -> None:
    """极简 .env 读取，仅用于本地调试；CI 上由 Secrets 注入环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="美股指数日报邮件")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="只生成不发送")
    parser.add_argument("--out", help="将 HTML 写入文件以便预览")
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使最新收盘日不是当天（休市或数据未更新）也照常出报",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv(ROOT / ".env")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    start = date.fromisoformat(str(cfg["history"]["start"]))
    rcfg = cfg.get("report") or {}
    lookbacks = tuple(rcfg.get("lookback_days") or (5, 20, 60))
    max_day_pct = float(rcfg.get("sanity_max_day_pct", 20.0))

    reports = []
    for item in cfg["indices"]:
        bars, source = fetch_history(item["code"], item.get("yahoo", ""), start)
        log.info(
            "%s: %d 根日线 (%s ~ %s) 来自 %s",
            item["name"], len(bars), bars[0].day, bars[-1].day, source,
        )
        reports.append(
            build_report(item["name"], bars, source, lookbacks, max_day_pct)
        )

    # 交易日判定不依赖假日表：拿最新一根日线的日期与美东当日比对即可。
    today_et = datetime.now(ET).date()
    trade_day = max(r.last_day for r in reports)
    if trade_day != today_et and not args.force:
        reason = "周末" if today_et.weekday() >= 5 else "美股休市或数据尚未更新"
        log.info("最新收盘日 %s 不是美东当日 %s（%s），跳过发送", trade_day, today_et, reason)
        return 0

    stale = [r.name for r in reports if r.last_day != trade_day]
    if stale:
        log.warning("以下指数的收盘日落后于 %s: %s", trade_day, "、".join(stale))

    html = render.render_html(reports, str(trade_day), datetime.now(CN))
    text = render.render_text(reports, str(trade_day))
    subject = render.render_subject(
        (cfg.get("email") or {}).get("subject_prefix", "[美股指数]"),
        reports,
        str(trade_day),
    )

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(html, encoding="utf-8")
        log.info("HTML 已写入 %s", args.out)

    if args.dry_run:
        print(subject)
        print()
        print(text)
        return 0

    settings = mailer.SmtpSettings.from_env((cfg.get("email") or {}).get("to"))
    mailer.send(settings, subject, html, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
