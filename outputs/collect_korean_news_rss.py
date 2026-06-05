#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collect Korean news articles from RSS feeds for the last N hours and save JSON.

Usage:
    pip install -r requirements.txt
    python collect_korean_news_rss.py --hours 24 --output news_recent_24h.json
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import feedparser
except ImportError:
    print("feedparser가 설치되어 있지 않습니다. 먼저 `pip install feedparser`를 실행하세요.", file=sys.stderr)
    raise

try:
    import requests
except ImportError:
    requests = None


KST = timezone(timedelta(hours=9), "KST")


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str


CATEGORIES: dict[str, list[FeedSource]] = {
    "거시경제·금융정책": [
        FeedSource("한국은행 보도자료(통화정책)", "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690"),
        FeedSource("한국은행 보도자료(경제통계)", "https://www.bok.or.kr/portal/bbs/B0000501/news.rss?menuNo=201264"),
        FeedSource("한국은행 경제전망보고서", "https://www.bok.or.kr/portal/bbs/P0002359/news.rss?menuNo=200066"),
        FeedSource("정책브리핑 금융위원회", "https://www.korea.kr/rss/dept_fsc.xml"),
        FeedSource("정책브리핑 보도자료", "https://www.korea.kr/rss/pressrelease.xml"),
        FeedSource("한국경제 경제", "https://www.hankyung.com/feed/economy"),
        FeedSource("한국경제 증권", "https://www.hankyung.com/feed/finance"),
        FeedSource("매일경제 경제", "https://www.mk.co.kr/rss/30100041/"),
    ],
    "은행·금융산업": [
        FeedSource("뉴시스 금융", "https://www.newsis.com/RSS/bank.xml"),
        FeedSource("파이낸셜뉴스 금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml"),
        FeedSource("한국경제 증권", "https://www.hankyung.com/feed/finance"),
        FeedSource("한국경제 경제", "https://www.hankyung.com/feed/economy"),
        FeedSource("매일경제 기업·경영", "https://www.mk.co.kr/rss/50100032/"),
        FeedSource("매일경제 증권", "https://www.mk.co.kr/rss/50200011/"),
    ],
    "금융권 주요 이슈": [
        FeedSource("과기정통부 보도자료", "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=94"),
        FeedSource("과기정통부 정보통신", "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=67"),
        FeedSource("한국경제 IT", "https://www.hankyung.com/feed/it"),
        FeedSource("파이낸셜뉴스 IT", "https://www.fnnews.com/rss/r20/fn_realnews_it.xml"),
        FeedSource("파이낸셜뉴스 블록포스트", "https://www.fnnews.com/rss/r20/fn_realnews_blockpost.xml"),
        FeedSource("뉴시스 IT·바이오", "https://www.newsis.com/RSS/health.xml"),
        FeedSource("매일경제 국제", "https://www.mk.co.kr/rss/30300018/"),
        FeedSource("매일경제 기업·경영", "https://www.mk.co.kr/rss/50100032/"),
        FeedSource("한국경제 국제", "https://www.hankyung.com/feed/international"),
        FeedSource("한국경제 경제", "https://www.hankyung.com/feed/economy"),
        FeedSource("파이낸셜뉴스 국제", "https://www.fnnews.com/rss/r20/fn_realnews_international.xml"),
        FeedSource("파이낸셜뉴스 산업", "https://www.fnnews.com/rss/r20/fn_realnews_industry.xml"),
        FeedSource("정책브리핑 중소벤처기업부", "https://www.korea.kr/rss/dept_mss.xml"),
        FeedSource("중소벤처기업부 보도자료", "https://mss.go.kr/rss/smba/board/86.do"),
    ],
}

ALWAYS_INCLUDE_SOURCES = (
    "한국은행",
    "정책브리핑 금융위원회",
)

FINANCE_KEYWORDS = (
    "금리",
    "기준금리",
    "통화정책",
    "물가",
    "환율",
    "외환",
    "거시",
    "경기",
    "GDP",
    "가계부채",
    "금융",
    "은행",
    "은행권",
    "금융권",
    "금융위",
    "금융위원회",
    "금감원",
    "금융감독원",
    "규제",
    "대출",
    "예금",
    "수신",
    "여신",
    "채권",
    "증권",
    "보험",
    "카드",
    "캐피탈",
    "저축은행",
    "상호금융",
    "핀테크",
    "마이데이터",
    "오픈뱅킹",
    "디지털금융",
    "가상자산",
    "토큰증권",
    "STO",
    "CBDC",
    "블록체인",
    "AI",
    "인공지능",
    "리스크",
    "건전성",
    "연체율",
    "충당금",
    "순이자마진",
    "NIM",
    "수익성",
    "자본비율",
    "BIS",
    "글로벌",
    "해외진출",
    "신사업",
    "M&A",
    "ESG",
    "녹색금융",
    "상생금융",
    "소상공인",
    "중소기업",
    "취약계층",
    "미국 연준",
    "Fed",
    "CPI",
    "고용지표",
    "국채금리",
    "은행 실적",
    "연체율",
    "충당금",
    "가계대출",
    "기업대출",
    "예대금리차",
    "DSR",
    "바젤Ⅲ",
    "금융소비자보호",
    "생성형 AI",
    "금융 AI",
    "디지털 전환",
    "DX",
    "인터넷은행",
    "카카오뱅크",
    "토스",
    "케이뱅크",
    "KB금융",
    "KB금융그룹",
    "신한금융",
    "신한금융그룹",
    "하나금융",
    "하나금융그룹",
    "우리금융",
    "우리금융그룹",
    "NH농협금융",
    "NH농협금융지주",
    "IBK기업은행",
    "조직개편",
    "AI 전략",
    "반도체",
    "배터리",
    "자동차",
    "조선",
    "부동산",
    "수출",
)

CATEGORY_PRIORITY_KEYWORDS = {
    "거시경제·금융정책": (
        "기준금리",
        "금리",
        "통화정책",
        "미국 연준",
        "연준",
        "Fed",
        "물가",
        "CPI",
        "환율",
        "GDP",
        "고용지표",
        "국채금리",
        "한국은행",
        "금융위원회",
        "금융감독원",
        "금융정책",
        "금융규제",
        "DSR",
        "스트레스 DSR",
        "가계부채",
        "바젤",
        "금융소비자보호",
    ),
    "은행·금융산업": (
        "은행",
        "은행권",
        "금융지주",
        "은행 실적",
        "실적",
        "NIM",
        "순이자마진",
        "연체율",
        "충당금",
        "대손비용",
        "가계대출",
        "기업대출",
        "예대금리차",
        "수익성",
        "여신",
        "수신",
        "저축은행",
        "KB금융",
        "신한금융",
        "하나금융",
        "우리금융",
        "NH농협금융",
        "IBK기업은행",
        "기업은행",
    ),
    "금융권 주요 이슈": (
        "생성형 AI",
        "금융 AI",
        "AI",
        "핀테크",
        "마이데이터",
        "오픈뱅킹",
        "디지털",
        "DX",
        "인터넷은행",
        "카카오뱅크",
        "토스",
        "케이뱅크",
        "반도체",
        "배터리",
        "자동차",
        "조선",
        "부동산",
        "중소기업",
        "수출",
        "증시",
        "코스피",
        "ETF",
        "ESG",
        "상생금융",
        "해외진출",
        "신사업",
    ),
}


def keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    haystack = text.lower()
    score = 0
    for keyword in keywords:
        keyword_text = keyword.lower()
        if keyword_text in haystack:
            score += 1 + haystack.count(keyword_text)
    return score


def category_priority_score(item: dict[str, Any], category: str) -> int:
    keywords = CATEGORY_PRIORITY_KEYWORDS.get(category, ())
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    source = f"{item.get('source', '')} {item.get('feed_title', '')}"
    return (
        keyword_score(title, keywords) * 4
        + keyword_score(source, keywords) * 2
        + keyword_score(summary, keywords)
    )


TOPIC_STOPWORDS = {
    "속보",
    "단독",
    "종합",
    "종합1보",
    "종합2보",
    "1보",
    "2보",
    "3보",
    "차기",
    "전",
    "현",
    "관련",
    "기자",
    "뉴스",
    "금융권",
    "은행권",
    "업계",
}


def normalize_topic_text(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "여신금융협회": "여신협회",
        "KB 금융": "KB금융",
        "非": "비",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", " ", cleaned).lower()
    return " ".join(cleaned.split())


def topic_tokens(item: dict[str, Any]) -> set[str]:
    normalized = normalize_topic_text(str(item.get("title") or ""))
    tokens: set[str] = set()
    for token in re.findall(r"[0-9a-z가-힣]{2,}", normalized):
        token = re.sub(r"(에는|에서|으로|에게|까지|부터|에도|이다|한다|했다|됐다|되다|에|은|는|이|가|을|를|의|와|과|로)$", "", token)
        if len(token) >= 2 and token not in TOPIC_STOPWORDS:
            tokens.add(token)
    return tokens


def is_similar_topic(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_title = normalize_topic_text(str(left.get("title") or ""))
    right_title = normalize_topic_text(str(right.get("title") or ""))
    if not left_title or not right_title:
        return False
    if left_title in right_title or right_title in left_title:
        return True
    title_similarity = SequenceMatcher(None, left_title, right_title).ratio()
    if title_similarity >= 0.72:
        return True

    left_tokens = topic_tokens(left)
    right_tokens = topic_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    common = left_tokens & right_tokens
    overlap = len(common) / max(1, min(len(left_tokens), len(right_tokens)))
    return len(common) >= 2 and (overlap >= 0.4 or title_similarity >= 0.5)


def dedupe_similar_topics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in items:
        if any(is_similar_topic(item, existing) for existing in kept):
            item["duplicate_topic_skipped"] = True
            continue
        kept.append(item)
    return kept


def is_relevant_finance_item(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "")
    if any(source.startswith(prefix) for prefix in ALWAYS_INCLUDE_SOURCES):
        return True
    haystack = f"{item.get('title', '')}\n{item.get('summary', '')}\n{item.get('feed_title', '')}".lower()
    return any(keyword.lower() in haystack for keyword in FINANCE_KEYWORDS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_link(link: str) -> str:
    if not link:
        return ""
    parsed = urllib.parse.urlsplit(link.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered_query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urllib.parse.urlencode(filtered_query, doseq=True),
            "",
        )
    )


def first_text(entry: Any, *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if value:
            return str(value).strip()
    return ""


def entry_datetime_utc(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def fetch_feed(source: FeedSource, timeout: int, user_agent: str) -> bytes:
    if requests is not None:
        response = requests.get(
            source.url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content

    request = urllib.request.Request(
        source.url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_feed(category: str, source: FeedSource, data: bytes) -> tuple[list[dict[str, Any]], str, str | None]:
    parsed = feedparser.parse(data)
    feed_title = first_text(parsed.feed, "title") or source.name
    bozo_error = str(parsed.bozo_exception) if parsed.get("bozo") and parsed.get("bozo_exception") else None

    items: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_at = entry_datetime_utc(entry)
        link = first_text(entry, "link", "id")
        title = first_text(entry, "title")
        summary = first_text(entry, "summary", "description")

        items.append(
            {
                "category": category,
                "title": title,
                "link": link,
                "summary": summary,
                "published_at": iso_utc(published_at) if published_at else None,
                "published_at_kst": published_at.astimezone(KST).isoformat() if published_at else None,
                "source": source.name,
                "feed_title": feed_title,
                "feed_url": source.url,
            }
        )
    return items, feed_title, bozo_error


def collect_recent(hours: int, timeout: int, user_agent: str, verbose: bool, max_per_category: int = 0) -> dict[str, Any]:
    generated_at = utc_now()
    cutoff = generated_at - timedelta(hours=hours)

    seen: set[str] = set()
    items_by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORIES}
    feed_results: list[dict[str, Any]] = []

    for category, sources in CATEGORIES.items():
        for source in sources:
            result: dict[str, Any] = {
                "category": category,
                "source": source.name,
                "url": source.url,
                "ok": False,
                "fetched_items": 0,
                "recent_items": 0,
                "error": None,
            }
            try:
                data = fetch_feed(source, timeout=timeout, user_agent=user_agent)
                parsed_items, feed_title, bozo_error = parse_feed(category, source, data)
                result["ok"] = True
                result["feed_title"] = feed_title
                result["bozo_error"] = bozo_error
                result["fetched_items"] = len(parsed_items)

                for item in parsed_items:
                    if not item["published_at"]:
                        continue
                    published_at = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                    if published_at < cutoff:
                        continue

                    if not is_relevant_finance_item(item):
                        continue

                    dedupe_key = normalize_link(item["link"]) or f"{category}:{item['title']}"
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    item["category_priority_score"] = category_priority_score(item, category)
                    items_by_category[category].append(item)
                    result["recent_items"] += 1

            except (urllib.error.URLError, TimeoutError) as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 - per-feed isolation is intentional here.
                result["error"] = f"{type(exc).__name__}: {exc}"
                if verbose:
                    result["traceback"] = traceback.format_exc()

            feed_results.append(result)

    all_items: list[dict[str, Any]] = []
    for category, category_items in items_by_category.items():
        category_items.sort(
            key=lambda item: (item.get("category_priority_score", 0), item["published_at"] or ""),
            reverse=True,
        )
        category_items = dedupe_similar_topics(category_items)
        items_by_category[category] = category_items
        if max_per_category:
            items_by_category[category] = category_items[:max_per_category]
            category_items = items_by_category[category]
        all_items.extend(category_items)
    all_items.sort(key=lambda item: item["published_at"] or "", reverse=True)

    failed_feeds = [result for result in feed_results if not result["ok"]]
    return {
        "generated_at": iso_utc(generated_at),
        "generated_at_kst": generated_at.astimezone(KST).isoformat(),
        "window_hours": hours,
        "max_per_category": max_per_category,
        "cutoff": iso_utc(cutoff),
        "cutoff_kst": cutoff.astimezone(KST).isoformat(),
        "categories": list(CATEGORIES.keys()),
        "total_items": len(all_items),
        "totals_by_category": {category: len(items) for category, items in items_by_category.items()},
        "failed_feed_count": len(failed_feeds),
        "feed_results": feed_results,
        "items": all_items,
        "items_by_category": items_by_category,
    }


def save_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="최근 N시간 한국 뉴스 RSS를 카테고리별로 수집해 JSON으로 저장합니다.")
    parser.add_argument("--hours", type=int, default=24, help="최근 몇 시간 안의 기사만 저장할지 지정합니다. 기본값: 24")
    parser.add_argument("--timeout", type=int, default=15, help="RSS 피드 하나당 요청 제한 시간(초). 기본값: 15")
    parser.add_argument("--output", default="news_recent_24h.json", help="저장할 JSON 파일 경로. 기본값: news_recent_24h.json")
    parser.add_argument("--max-per-category", type=int, default=0, help="수집 단계에서 카테고리별 최대 후보 기사 수. 0이면 제한 없음")
    parser.add_argument("--verbose", action="store_true", help="예상 밖 예외의 traceback을 JSON에 포함합니다.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hours <= 0:
        print("--hours는 1 이상의 정수여야 합니다.", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("--timeout은 1 이상의 정수여야 합니다.", file=sys.stderr)
        return 2

    user_agent = "Mozilla/5.0 (compatible; KoreanNewsRSSCollector/1.0; +https://example.local)"
    payload = collect_recent(
        hours=args.hours,
        timeout=args.timeout,
        user_agent=user_agent,
        verbose=args.verbose,
        max_per_category=args.max_per_category,
    )
    output_path = Path(args.output)
    save_json(payload, output_path)

    print(f"저장 완료: {output_path.resolve()}")
    print(f"총 기사: {payload['total_items']}건")
    print(f"실패 피드: {payload['failed_feed_count']}개")
    for category, count in payload["totals_by_category"].items():
        print(f"- {category}: {count}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
