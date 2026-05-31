#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Post summarized Korean news JSON to a Discord webhook.

Reads DISCORD_WEBHOOK_URL from .env by default. Articles without summary_ko are
skipped because Discord messages should contain the Korean three-line summary.
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATEGORIES = [
    "통화 정책 및 거시경제",
    "금융 규제 및 정부정책",
    "디지털 금융 및 테크 트렌드",
    "은행 수익성 및 리스트 관리",
    "신사업 및 글로벌 전략",
    "ESG 및 상생금융",
]
CATEGORY_COLORS = {
    "통화 정책 및 거시경제": 0xDC2626,  # red
    "금융 규제 및 정부정책": 0xF97316,  # orange
    "디지털 금융 및 테크 트렌드": 0x2563EB,  # blue
    "은행 수익성 및 리스트 관리": 0x16A34A,  # green
    "신사업 및 글로벌 전략": 0x9333EA,  # purple
    "ESG 및 상생금융": 0x0891B2,  # teal
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def truncate(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def normalize_multiline(text: str, limit: int) -> str:
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n") if line.strip()]
    cleaned = "\n".join(lines)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def category_groups(data: dict[str, Any], limit_per_category: int = 0) -> dict[str, list[dict[str, Any]]]:
    grouped = {category: [] for category in CATEGORIES}
    for item in data.get("items", []):
        category = item.get("category")
        if category not in grouped:
            continue
        if not item.get("summary_ko"):
            continue
        grouped[category].append(item)

    for category, items in grouped.items():
        items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        if limit_per_category:
            grouped[category] = items[:limit_per_category]
    return grouped


def build_embed(item: dict[str, Any], category: str) -> dict[str, Any]:
    source = item.get("source") or item.get("feed_title") or "출처 미상"
    link = item.get("link") or ""
    summary_ko = normalize_multiline(item.get("summary_ko", ""), 3300)
    published_at_kst = item.get("published_at_kst") or ""

    description_parts = [summary_ko]
    if link:
        description_parts.append(f"[원문 출처 링크]({link})")
    description = "\n\n".join(part for part in description_parts if part)

    embed: dict[str, Any] = {
        "title": truncate(item.get("title", "제목 없음"), 256),
        "description": description[:4096],
        "color": CATEGORY_COLORS[category],
        "footer": {"text": truncate(f"{source} · {published_at_kst}", 2048)},
    }
    if link:
        embed["url"] = link
    return embed


def post_webhook(webhook_url: str, payload: dict[str, Any], timeout: int) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord HTTP {exc.code}: {body[:1000]}") from exc


def build_messages(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    total = sum(len(items) for items in grouped.values())
    header = {
        "content": (
            f"금융권·은행권 뉴스 브리핑 · {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}\n"
            f"총 {total}건"
        ),
        "allowed_mentions": {"parse": []},
    }
    messages.append(header)

    for category in CATEGORIES:
        items = grouped[category]
        if not items:
            continue
        chunks = chunked(items, 10)
        for index, chunk in enumerate(chunks, start=1):
            suffix = f" ({index}/{len(chunks)})" if len(chunks) > 1 else ""
            embeds = [build_embed(item, category) for item in chunk]
            messages.append(
                {
                    "content": f"**{category}** {len(items)}건{suffix}",
                    "embeds": embeds,
                    "allowed_mentions": {"parse": []},
                }
            )
    return messages


def post_news(args: argparse.Namespace) -> int:
    load_env_file(Path(args.env))
    webhook_url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        print(f"DISCORD_WEBHOOK_URL이 없습니다. {args.env} 파일에 DISCORD_WEBHOOK_URL=... 형태로 추가하세요.")
        return 2

    input_path = Path(args.input)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    grouped = category_groups(data, limit_per_category=args.limit_per_category)
    messages = build_messages(grouped)

    article_count = sum(len(items) for items in grouped.values())
    if article_count == 0:
        print("보낼 기사가 없습니다. summary_ko가 있는 기사만 Discord로 보냅니다.")
        print("먼저 summarize_news_with_llm.py를 실행해 summary_ko를 채워주세요.")
        return 1

    print(f"입력 파일: {input_path}")
    print(f"전송 기사: {article_count}건")
    print(f"전송 메시지: {len(messages)}개")
    for category, items in grouped.items():
        print(f"- {category}: {len(items)}건")

    if args.dry_run:
        print("\n--dry-run이라 실제 Discord 전송은 하지 않았습니다.")
        for idx, message in enumerate(messages, start=1):
            preview = textwrap.shorten(message.get("content", ""), width=100, placeholder="…")
            print(f"[{idx}/{len(messages)}] {preview} / embeds={len(message.get('embeds', []))}")
        return 0

    for idx, message in enumerate(messages, start=1):
        preview = textwrap.shorten(message.get("content", ""), width=80, placeholder="…")
        print(f"[{idx}/{len(messages)}] 전송 중: {preview}", flush=True)
        post_webhook(webhook_url, message, timeout=args.timeout)
        print(f"[{idx}/{len(messages)}] 전송 완료", flush=True)
        if idx < len(messages):
            time.sleep(args.sleep)

    print("Discord 전송 완료")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="요약/분류된 금융권·은행권 뉴스 JSON을 Discord 웹후크로 전송합니다.")
    parser.add_argument("--input", default="outputs/news_recent_24h.json", help="전송할 요약 JSON 경로")
    parser.add_argument("--env", default=".env", help="DISCORD_WEBHOOK_URL을 읽을 .env 파일 경로")
    parser.add_argument("--timeout", type=int, default=30, help="Discord 요청 타임아웃(초). 기본값: 30")
    parser.add_argument("--sleep", type=float, default=0.5, help="Discord 메시지 사이 대기 시간(초). 기본값: 0.5")
    parser.add_argument("--limit-per-category", type=int, default=0, help="카테고리별 최대 전송 기사 수. 0이면 제한 없음")
    parser.add_argument("--dry-run", action="store_true", help="실제 전송 없이 메시지 개수와 embed 분할만 확인")
    return parser.parse_args()


def main() -> int:
    return post_news(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
