#!/usr/bin/env python3
"""Explicit, resumable catalog backfill; no downloads or model calls.

Examples:
  python scripts/update_catalog.py --seed-only
  python scripts/update_catalog.py --file index.json
  python scripts/update_catalog.py --from-week 2025-W01 --to-week 2026-W43 --resume
"""
import argparse
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Match run.py's data directory before app.core captures environment values.
# Literal .env loading never executes a shell; explicit process values win.
from run import load_env_file
if (ROOT / ".env").is_file():
    load_env_file(ROOT / ".env")
configured_data = os.environ.get("PAPERSTUDIO_DATA_DIR")
if configured_data and not Path(configured_data).is_absolute():
    os.environ["PAPERSTUDIO_DATA_DIR"] = str((ROOT / configured_data).resolve())
from fastapi import HTTPException
from app import catalog, core


def week_keys(start, end):
    catalog.validate_week(start)
    catalog.validate_week(end)
    cursor = date.fromisocalendar(int(start[:4]), int(start[-2:]), 1)
    last = date.fromisocalendar(int(end[:4]), int(end[-2:]), 1)
    if cursor > last:
        raise ValueError("开始周不得晚于结束周")
    while cursor <= last:
        year, week, _ = cursor.isocalendar()
        yield f"{year}-W{week:02d}"
        cursor += timedelta(days=7)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--seed-only", action="store_true", help="从manifest导入本地全量快照；不联网刷新")
    parser.add_argument("--from-week")
    parser.add_argument("--to-week")
    parser.add_argument("--resume", action="store_true", help="跳过已有 HF 周快照记录的周（不代表 50 条完整）；补全/刷新时不加此选项")
    parser.add_argument("--delay", type=float, default=3, help="周请求之间的间隔（至少 1 秒）")
    args = parser.parse_args()
    if args.seed_only and (args.file or args.from_week or args.to_week):
        parser.error("--seed-only 不与文件或周范围混用")
    if not args.seed_only and bool(args.file) == bool(args.from_week or args.to_week):
        parser.error("选择 --file 或同时提供 --from-week/--to-week")
    if not args.seed_only and not args.file and (not args.from_week or not args.to_week):
        parser.error("周范围必须同时提供起止周")
    core.init_db()
    catalog.init_catalog()
    try:
        if args.seed_only:
            stats = catalog.stats()
            print(json.dumps({"total": stats["total"], "source_records": stats["source_records"], "import_errors": stats["import_errors"]}, ensure_ascii=False))
            return
        if args.file:
            if args.file.stat().st_size > catalog.MAX_BYTES:
                raise ValueError("文件超过 20 MiB")
            print(json.dumps(catalog.import_bytes(args.file.read_bytes(), args.file.name), ensure_ascii=False))
            return
        done = set(catalog.observed_hf_weeks()) if args.resume else set()
        first = True
        for week in week_keys(args.from_week, args.to_week):
            if week in done:
                print(json.dumps({"week": week, "status": "skipped"}))
                continue
            if not first:
                time.sleep(max(1, args.delay))
            first = False
            result = asyncio.run(catalog.refresh_hf(week))
            print(json.dumps({"week": week, **result}, ensure_ascii=False), flush=True)
    except (HTTPException, ValueError, OSError) as exc:
        print(str(exc.detail) if isinstance(exc, HTTPException) else str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

