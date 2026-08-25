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
    parser.add_argument(
        "--heartbeat",
        action="store_true",
        help="标记为周度心跳，用于确认定时任务仍在运行",
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

    # 单个指数失败不应连带其他指数：失败项记入 failures，在邮件中标注出来。
    reports, failures = [], []
    for item in cfg["indices"]:
        name = item["name"]
        try:
            bars, source = fetch_history(
                item["code"], item.get("eastmoney", ""), start
            )
            log.info(
                "%s: %d 根日线 (%s ~ %s) 来自 %s",
                name, len(bars), bars[0].day, bars[-1].day, source,
            )
            reports.append(build_report(name, bars, source, lookbacks, max_day_pct))
        except Exception as exc:  # noqa: BLE001
            log.error("%s: 获取或计算失败，本次跳过该指数: %s", name, exc)
            failures.append((name, str(exc)))

    if not reports:
        log.error("所有指数均失败，不发送邮件")
        return 1

    # 交易日判定不依赖假日表：拿最新一根日线的日期与美东当日比对即可。
    today_et = datetime.now(ET).date()
    trade_day = max(r.last_day for r in reports)
    # 心跳的意义在于「一定会到达」，因此不受交易日判定的影响。
    if trade_day != today_et and not (args.force or args.heartbeat):
        reason = "周末" if today_et.weekday() >= 5 else "美股休市或数据尚未更新"
        log.info("最新收盘日 %s 不是美东当日 %s（%s），跳过发送", trade_day, today_et, reason)
        return 0

    stale = [r.name for r in reports if r.last_day != trade_day]
    if stale:
        log.warning("以下指数的收盘日落后于 %s: %s", trade_day, "、".join(stale))

    html = render.render_html(
        reports, str(trade_day), datetime.now(CN), failures, args.heartbeat
    )
    text = render.render_text(reports, str(trade_day), failures)
    subject = render.render_subject(
        (cfg.get("email") or {}).get("subject_prefix", "[美股指数]"),
        reports,
        str(trade_day),
        failures,
        args.heartbeat,
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
