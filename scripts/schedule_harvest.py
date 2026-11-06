#!/usr/bin/env python3
"""Write the harvest launch instant once, at deployment (never at server startup)."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("Asia/Shanghai")
SEASON_ID = "autumn_harvest_festival"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/seasons.json"


def launch_window(launched_at: datetime) -> tuple[datetime, datetime]:
    if launched_at.utcoffset() is None:
        raise ValueError("上线时间必须包含时区")
    start = launched_at.astimezone(ZONE).replace(microsecond=0)
    end = datetime.combine(start.date() + timedelta(days=45), time(4), ZONE)
    return start, end


def schedule(payload: dict, launched_at: datetime) -> dict:
    # A published schedule is immutable here: rerunning a deployment must not
    # silently move the season, reinterpret gacha history or reset the clock.
    result = json.loads(json.dumps(payload))
    season = next(entry for entry in result["seasons"] if entry["id"] == SEASON_ID)
    if season.get("enabled", True):
        raise ValueError("活动档期已经启用；保留现有发布时间，不重复排期")
    start, end = launch_window(launched_at)
    season.update(enabled=True, starts_at=start.isoformat(), ends_at=end.isoformat())
    season["note"] = (
        "实际发布时间起开放；上线日期加 45 天的北京时间 04:00 结束。"
        "五位伙伴各轮替三次，共十五期；每三天 04:00 换期。"
        "结束后采集点关闭，菜谱与工位保留。"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-at", type=datetime.fromisoformat,
                        help="带时区的实际发布时间；默认执行时刻")
    parser.add_argument("--seasons", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--write", action="store_true", help="正式发布时写入并启用；默认只预览")
    args = parser.parse_args()
    payload = json.loads(args.seasons.read_text(encoding="utf-8"))
    try:
        result = schedule(payload, args.launch_at or datetime.now(ZONE))
    except ValueError as exc:
        parser.error(str(exc))
    season = next(entry for entry in result["seasons"] if entry["id"] == SEASON_ID)
    print(json.dumps(season, ensure_ascii=False, indent=2))
    if args.write:
        # Same-directory replace prevents readers from seeing partial JSON.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.seasons.parent,
                                         prefix=".harvest-schedule-", delete=False) as output:
            output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            temporary = Path(output.name)
        temporary.replace(args.seasons)
        print("档期已保存；后续重启和发布不会重新计时。")
    else:
        print("仅预览，未改变档期。正式发布时加 --write。")


if __name__ == "__main__":
    main()
