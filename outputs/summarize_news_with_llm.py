#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Summarize collected RSS news articles with an OpenAI-compatible LLM API.

The script reads LLM_API_KEY from .env, sends each article to the API, and
updates the original JSON with:
  - summary_ko: three Korean lines (fact, context, implication)
  - category: one of the banking interview categories
  - interview_questions: interview questions based on the article

Optional .env values:
  LLM_PROVIDER=gemini or openai
  LLM_API_URL=https://api.openai.com/v1/chat/completions
  LLM_MODEL=gemini-2.5-flash or gpt-4o-mini
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATEGORIES = [
    "거시경제 · 금융시장",
    "은행 · 금융산업",
    "금융정책 · 규제",
    "디지털금융 · AI · 핀테크",
    "지원 기업 · 금융사 동향",
    "산업 · 기업 이슈",
]
DEFAULT_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


PROMPT_TEMPLATE = """다음 한국 뉴스 기사 1건을 읽고 JSON만 출력해줘.

요구사항:
- summary_ko는 한국어 3줄이어야 한다.
- 1줄째는 "사실: "로 시작하고 핵심 사실만 쓴다.
- 2줄째는 "맥락: "으로 시작하고 배경/흐름을 쓴다.
- 3줄째는 "시사점: "으로 시작하고 의미/영향을 쓴다.
- category는 아래 6개 중 정확히 하나만 고른다.
- interview_questions는 기사 내용과 은행권 면접 관점에 맞는 한국어 질문 2개 배열로 만든다.
- 출력은 JSON 객체 하나만 허용한다.

카테고리:
- 거시경제 · 금융시장: 기준금리, 미국 연준(Fed), 물가(CPI), 환율, GDP, 고용지표, 국채금리
- 은행 · 금융산업: 은행 실적, NIM, 연체율, 충당금, 가계대출, 기업대출, 예대금리차
- 금융정책 · 규제: 금융위원회, 금융감독원, 한국은행, DSR, 가계부채, 바젤Ⅲ, 금융소비자보호
- 디지털금융 · AI · 핀테크: 생성형 AI, 금융 AI, 마이데이터, 오픈뱅킹, 디지털 전환(DX), 인터넷은행, 카카오뱅크, 토스, 케이뱅크
- 지원 기업 · 금융사 동향: KB금융그룹, 신한금융그룹, 하나금융그룹, 우리금융그룹, NH농협금융지주, IBK기업은행, 실적, 신사업, ESG, 해외진출, 조직개편, AI 전략
- 산업 · 기업 이슈: 반도체, 배터리, 자동차, 조선, 부동산, 중소기업, 수출

출력 형식:
{{"summary_ko":"사실: ...\\n맥락: ...\\n시사점: ...","category":"거시경제 · 금융시장","interview_questions":["금리 인하가 은행 수익성에 미치는 영향은?","환율 상승이 국내 경제에 미치는 영향은?"]}}

기사:
제목: {title}
기존 RSS 분류: {source_category}
출처: {source}
게시시각: {published_at_kst}
본문/요약:
{summary}
"""


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


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else cleaned


def normalize_summary(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    if len(lines) < 3:
        sentences = re.split(r"(?<=[.!?。！？다])\s+", " ".join(lines))
        lines = [s.strip() for s in sentences if s.strip()]
    lines = (lines + [""] * 3)[:3]

    labels = ["사실:", "맥락:", "시사점:"]
    normalized: list[str] = []
    for label, line in zip(labels, lines):
        line = re.sub(r"^(사실|맥락|시사점)\s*[:：-]?\s*", "", line).strip()
        normalized.append(f"{label} {line}".rstrip())
    return "\n".join(normalized)


def normalize_interview_questions(value: Any) -> list[str]:
    if isinstance(value, list):
        questions = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value or "").replace("\r\n", "\n")
        questions = [line.strip(" -•\t") for line in text.split("\n") if line.strip(" -•\t")]

    normalized: list[str] = []
    for question in questions:
        if not question.endswith("?"):
            question = question.rstrip(".") + "?"
        normalized.append(question)
    return normalized[:2]


def parse_llm_content(content: str) -> dict[str, Any]:
    parsed = json.loads(strip_code_fence(content))
    summary_ko = normalize_summary(str(parsed.get("summary_ko", "")))
    category = str(parsed.get("category", "")).strip()
    if category not in CATEGORIES:
        raise ValueError(f"LLM이 허용되지 않은 category를 반환했습니다: {category!r}")
    interview_questions = normalize_interview_questions(parsed.get("interview_questions"))
    if not interview_questions:
        interview_questions = [
            f"이 뉴스가 {category} 관점에서 은행권에 주는 영향은?",
            "면접에서 이 이슈를 은행 지원 동기와 어떻게 연결해 설명할 수 있을까?",
        ]
    return {"summary_ko": summary_ko, "category": category, "interview_questions": interview_questions}


def call_openai_compatible_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if "openrouter.ai" in api_url:
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_SITE_URL", "https://github.com")
        headers["X-Title"] = os.environ.get("OPENROUTER_APP_NAME", "Daily Bank Finance News Brief")

    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "너는 한국 뉴스 편집자다. 반드시 유효한 JSON만 출력한다.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body[:1000]}") from exc

    payload = json.loads(response_body)
    content = payload["choices"][0]["message"]["content"]
    return parse_llm_content(content)


def call_gemini_llm(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict[str, str]:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "너는 한국 뉴스 편집자다. 반드시 유효한 JSON만 출력한다.\n\n"
                            + prompt
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {error_body[:1000]}") from exc

    payload = json.loads(response_body)
    content = payload["candidates"][0]["content"]["parts"][0]["text"]
    return parse_llm_content(content)


def build_prompt(item: dict[str, Any]) -> str:
    source_category = item.get("source_category") or item.get("category") or ""
    return PROMPT_TEMPLATE.format(
        title=str(item.get("title", ""))[:500],
        source_category=str(source_category),
        source=str(item.get("source", "")),
        published_at_kst=str(item.get("published_at_kst", "")),
        summary=str(item.get("summary", ""))[:3500],
    )


def call_llm(
    *,
    provider: str,
    api_url: str,
    api_key: str,
    model: str,
    item: dict[str, Any],
    timeout: int,
) -> dict[str, str]:
    prompt = build_prompt(item)
    if provider == "gemini":
        return call_gemini_llm(api_key=api_key, model=model, prompt=prompt, timeout=timeout)
    return call_openai_compatible_llm(api_url=api_url, api_key=api_key, model=model, prompt=prompt, timeout=timeout)


def is_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    retry_markers = (
        "429",
        "rate limit",
        "temporarily",
        "timeout",
        "timed out",
        "500",
        "502",
        "503",
        "504",
        "connection reset",
        "remote disconnected",
    )
    return any(marker in text for marker in retry_markers)


def rebuild_items_by_category(data: dict[str, Any]) -> None:
    items_by_category = {category: [] for category in CATEGORIES}
    for item in data.get("items", []):
        category = item.get("category")
        if category in items_by_category:
            items_by_category[category].append(item)
    data["items_by_category"] = items_by_category
    data["totals_by_category"] = {category: len(items) for category, items in items_by_category.items()}


def summarize_items(args: argparse.Namespace) -> int:
    env_path = Path(args.env)
    load_env_file(env_path)

    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    if not api_key:
        print(f"LLM_API_KEY가 없습니다. {env_path} 파일에 LLM_API_KEY=... 형태로 추가하세요.")
        return 2

    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "gemini" if api_key.startswith("AQ.") else "openai"
    if provider not in {"gemini", "openai"}:
        print("LLM_PROVIDER는 gemini 또는 openai 중 하나여야 합니다.")
        return 2

    if provider == "gemini":
        api_url = (os.environ.get("LLM_API_URL") or "https://generativelanguage.googleapis.com/v1beta").strip()
        model = (os.environ.get("LLM_MODEL") or DEFAULT_GEMINI_MODEL).strip()
    else:
        api_url = (os.environ.get("LLM_API_URL") or os.environ.get("LLM_ENDPOINT") or DEFAULT_OPENAI_API_URL).strip()
        model = (os.environ.get("LLM_MODEL") or DEFAULT_OPENAI_MODEL).strip()

    input_path = Path(args.input)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("입력 JSON의 items가 list가 아닙니다.")

    targets: list[dict[str, Any]] = []
    for item in items:
        if "source_category" not in item:
            item["source_category"] = item.get("category")
        if args.force or not item.get("summary_ko"):
            targets.append(item)

    if args.limit:
        targets = targets[: args.limit]

    total = len(targets)
    print(f"입력 파일: {input_path}")
    print(f"프로바이더: {provider}")
    print(f"모델: {model}")
    print(f"요약 대상: {total}건")

    success_count = 0
    failure_count = 0
    for idx, item in enumerate(targets, start=1):
        title = str(item.get("title", "")).replace("\n", " ")[:60]
        print(f"[{idx}/{total}] 요청 중: {title}", flush=True)
        try:
            result = None
            for attempt in range(args.retries + 1):
                try:
                    result = call_llm(
                        provider=provider,
                        api_url=api_url,
                        api_key=api_key,
                        model=model,
                        item=item,
                        timeout=args.timeout,
                    )
                    break
                except Exception as retry_exc:
                    if attempt >= args.retries or not is_retryable_error(retry_exc):
                        raise
                    wait_seconds = args.retry_sleep * (2**attempt)
                    print(f"[{idx}/{total}] 재시도 대기 {wait_seconds:.1f}초: {type(retry_exc).__name__}: {retry_exc}", flush=True)
                    time.sleep(wait_seconds)

            if result is None:
                raise RuntimeError("LLM 응답을 받지 못했습니다.")
            item["summary_ko"] = result["summary_ko"]
            item["category"] = result["category"]
            item["interview_questions"] = result["interview_questions"]
            item.pop("summary_error", None)
            success_count += 1
            print(f"[{idx}/{total}] 완료: {result['category']} / {title}", flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the batch.
            item["summary_error"] = f"{type(exc).__name__}: {exc}"
            failure_count += 1
            print(f"[{idx}/{total}] 실패: {title} -> {item['summary_error']}", flush=True)

        data["llm_summary"] = {
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": provider,
            "model": model,
            "api_url": api_url,
            "sleep_seconds": args.sleep,
            "processed_target_count": total,
        }
        rebuild_items_by_category(data)
        atomic_write_json(input_path, data)

        if idx < total:
            time.sleep(args.sleep)

    print(f"저장 완료: {input_path.resolve()}")
    print(f"요약 성공: {success_count}건")
    print(f"요약 실패: {failure_count}건")
    if total > 0 and success_count == 0:
        print("요약된 기사가 0건이라 다음 단계로 진행할 수 없습니다. 위의 첫 실패 원인을 확인하세요.")
        return 1
    if total > 0:
        success_ratio = success_count / total
        print(f"요약 성공률: {success_ratio:.1%}")
        if success_ratio < args.min_success_ratio:
            print(f"요약 성공률이 기준({args.min_success_ratio:.0%})보다 낮아 다음 단계로 진행하지 않습니다.")
            return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="수집된 금융권/은행권 뉴스 JSON을 LLM으로 한국어 3줄 요약하고 6개 카테고리로 분류합니다.")
    parser.add_argument("--input", default="outputs/news_recent_24h.json", help="업데이트할 원본 JSON 경로")
    parser.add_argument("--env", default=".env", help="LLM_API_KEY를 읽을 .env 파일 경로")
    parser.add_argument("--sleep", type=float, default=0.5, help="LLM 요청 사이 대기 시간(초). 기본값: 0.5")
    parser.add_argument("--timeout", type=int, default=60, help="LLM API 요청 타임아웃(초). 기본값: 60")
    parser.add_argument("--retries", type=int, default=3, help="429/일시 오류 발생 시 기사별 재시도 횟수. 기본값: 3")
    parser.add_argument("--retry-sleep", type=float, default=5.0, help="재시도 기본 대기 시간(초). 기본값: 5.0")
    parser.add_argument("--min-success-ratio", type=float, default=0.8, help="다음 단계로 진행할 최소 요약 성공률. 기본값: 0.8")
    parser.add_argument("--limit", type=int, default=0, help="테스트용 처리 개수 제한. 0이면 전체 처리")
    parser.add_argument("--force", action="store_true", help="이미 summary_ko가 있는 기사도 다시 처리")
    return parser.parse_args()


def main() -> int:
    return summarize_items(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
