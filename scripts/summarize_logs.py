"""Summarize the access log (logs/access.jsonl).

Run on the deploy host to see daily JPO quota consumption, top endpoints,
and error breakdown:

    python scripts/summarize_logs.py
    python scripts/summarize_logs.py --path logs/access.jsonl --days 1
    python scripts/summarize_logs.py --days 7  # weekly view

The log format is documented in src/ip_mcp/access_log.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize JSONL access log.")
    parser.add_argument(
        "--path",
        default="logs/access.jsonl",
        help="path to JSONL access log (default: logs/access.jsonl)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="window in days from now (default: 1 = last 24h)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="show top N endpoints (default: 20)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"no log file at {path}", file=sys.stderr)
        return 1

    cutoff = datetime.now(UTC) - timedelta(days=args.days)

    by_endpoint: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    remain_by_endpoint: dict[str, str] = {}
    elapsed_by_endpoint: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    total = 0

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            try:
                ts = datetime.fromisoformat(rec["ts"])
            except (KeyError, ValueError):
                skipped += 1
                continue
            if ts < cutoff:
                continue
            total += 1
            ep = rec.get("endpoint", "?")
            by_endpoint[ep] += 1
            outcomes[rec.get("outcome", "?")] += 1
            sources[rec.get("source", "?")] += 1
            elapsed_by_endpoint[ep].append(float(rec.get("elapsed_ms", 0)))
            remain = rec.get("remain_today")
            if remain:
                remain_by_endpoint[ep] = str(remain)

    print(f"=== access log summary - last {args.days} day(s) ===")
    print(f"window starts: {cutoff.isoformat()}")
    print(f"total calls:   {total}")
    if skipped:
        print(f"(skipped {skipped} unparseable lines)")
    print()

    print("by source:")
    for src, n in sources.most_common():
        print(f"  {n:>6}  {src}")
    print()

    print("by outcome:")
    for outcome, n in outcomes.most_common():
        print(f"  {n:>6}  {outcome}")
    print()

    print(f"top {args.top} endpoints:")
    for ep, n in by_endpoint.most_common(args.top):
        elapsed = elapsed_by_endpoint[ep]
        avg = sum(elapsed) / len(elapsed) if elapsed else 0.0
        remain = remain_by_endpoint.get(ep)
        suffix = f"  remain={remain}" if remain else ""
        print(f"  {n:>5}  avg {avg:>6.0f} ms  {ep}{suffix}")

    if remain_by_endpoint:
        print()
        print("latest JPO remainAccessCount per endpoint:")
        for ep, remain in sorted(remain_by_endpoint.items()):
            print(f"  {remain:>5}  {ep}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
