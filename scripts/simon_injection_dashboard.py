#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from open_webui.internal.db import get_db_context  # noqa: E402
from open_webui.models.chats import Chat  # noqa: E402


def _fmt_ts(value) -> str:
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


def _print_chat_summary(chat, history_limit: int) -> None:
    meta = dict(chat.meta or {})
    simon = dict(meta.get("simon") or {})
    last = dict(simon.get("last") or {})
    injection = dict(last.get("injection") or {})
    if not injection:
        return

    print(f"chat_id={chat.id}")
    print(f"updated_at={_fmt_ts(chat.updated_at)}")
    print(f"gate_reason={last.get('gate_reason')}")
    print(
        "injection:"
        f" scope={injection.get('scope_key')}"
        f" frozen_source={injection.get('frozen_source')}"
        f" frozen_count={injection.get('frozen_anchor_count')}"
        f" combined_count={injection.get('combined_anchor_count')}"
        f" on_demand={injection.get('on_demand')}"
        f" on_demand_reason={injection.get('on_demand_reason')}"
        f" combined_hash={injection.get('combined_anchor_hash')}"
    )
    preview = injection.get("combined_anchor_preview") or []
    if preview:
        for idx, line in enumerate(preview, start=1):
            print(f"  preview[{idx}] {line}")

    history = list(simon.get("injection_history") or [])
    if history_limit > 0 and history:
        print("recent_injections:")
        for item in history[-history_limit:]:
            print(
                "  "
                + json.dumps(
                    {
                        "timestamp": _fmt_ts(item.get("timestamp")),
                        "scope_key": item.get("scope_key"),
                        "frozen_source": item.get("frozen_source"),
                        "frozen_anchor_count": item.get("frozen_anchor_count"),
                        "combined_anchor_count": item.get("combined_anchor_count"),
                        "on_demand": item.get("on_demand"),
                        "on_demand_reason": item.get("on_demand_reason"),
                        "combined_anchor_hash": item.get("combined_anchor_hash"),
                        "query_hash": item.get("query_hash"),
                    },
                    ensure_ascii=False,
                )
            )
    print("-" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Simon injection telemetry from chat meta.")
    parser.add_argument("--chat-id", type=str, default="", help="Inspect one chat id")
    parser.add_argument("--limit", type=int, default=20, help="Number of latest chats to scan")
    parser.add_argument(
        "--history",
        type=int,
        default=8,
        help="How many recent injection-history entries to print per chat",
    )
    args = parser.parse_args()

    with get_db_context() as db:
        if args.chat_id:
            chat = db.get(Chat, args.chat_id)
            if not chat:
                print(f"chat not found: {args.chat_id}")
                return
            _print_chat_summary(chat, history_limit=max(0, int(args.history)))
            return

        rows = (
            db.execute(
                select(Chat).order_by(Chat.updated_at.desc()).limit(max(1, int(args.limit)))
            )
            .scalars()
            .all()
        )
        for chat in rows:
            _print_chat_summary(chat, history_limit=max(0, int(args.history)))


if __name__ == "__main__":
    main()
