#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Append summarized Korean news JSON to Google Sheets.

Reads from .env:
  GOOGLE_CREDENTIALS=raw credentials.json content
  GOOGLE_CREDENTIALS_PATH=path/to/credentials.json
  SPREADSHEET_ID=google_sheet_id

Managed worksheets:
  - 사용된뉴스: 발행일, 카테고리, 제목, 한국어 3줄 요약, 원문 링크, 출처 매체
  - 브리핑히스토리: 발송시각, 기사건수, 상태
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USED_NEWS_SHEET = "사용된뉴스"
HISTORY_SHEET = "브리핑히스토리"
USED_NEWS_HEADERS = ["발행일", "카테고리", "제목", "한국어 3줄 요약", "원문 링크", "출처 매체"]
HISTORY_HEADERS = ["발송시각", "기사건수", "상태"]


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


def load_google_modules() -> tuple[Any, Any, Any]:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from gspread.exceptions import APIError
    except ModuleNotFoundError as exc:
        missing = exc.name or "gspread/google-auth"
        print(f"필요한 패키지가 없습니다: {missing}")
        print("먼저 `pip install -r outputs/requirements.txt`를 실행하세요.")
        raise SystemExit(2) from exc
    return gspread, Credentials, APIError


def resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def load_credentials_info(env_path: Path) -> dict[str, Any]:
    credentials_text = (os.environ.get("GOOGLE_CREDENTIALS") or "").strip()
    if credentials_text:
        try:
            return json.loads(credentials_text)
        except json.JSONDecodeError as exc:
            print("GOOGLE_CREDENTIALS를 JSON으로 파싱할 수 없습니다. credentials.json 파일 내용 전체를 Secret에 넣었는지 확인하세요.")
            raise SystemExit(2) from exc

    credentials_path_text = (os.environ.get("GOOGLE_CREDENTIALS_PATH") or "").strip()
    if not credentials_path_text:
        print(
            f"GOOGLE_CREDENTIALS 또는 GOOGLE_CREDENTIALS_PATH가 없습니다. "
            f"{env_path} 파일이나 GitHub Secrets에 credentials 정보를 추가하세요."
        )
        raise SystemExit(2)

    credentials_path = resolve_path(credentials_path_text, env_path.parent.resolve())
    if not credentials_path.exists():
        print(f"credentials.json 파일을 찾을 수 없습니다: {credentials_path}")
        raise SystemExit(2)
    return json.loads(credentials_path.read_text(encoding="utf-8"))


def service_email_from_env(env_path: Path) -> str | None:
    load_env_file(env_path)
    try:
        credentials_info = load_credentials_info(env_path)
    except Exception:
        return None
    return credentials_info.get("client_email")


def friendly_permission_message(exc: Exception, service_email: str | None) -> str:
    email = service_email or "credentials.json의 client_email"
    return (
        "Google Sheets 권한 오류가 발생했습니다.\n"
        f"서비스 계정 이메일 `{email}`을 Google Sheets 문서에 편집자로 공유했는지 확인하세요.\n"
        "공유 후에도 실패하면 SPREADSHEET_ID가 올바른지, Google Sheets API가 활성화되어 있는지 확인하세요.\n"
        f"원본 오류: {type(exc).__name__}: {exc}"
    )


def open_spreadsheet(env_path: Path) -> tuple[Any, Any, str | None]:
    load_env_file(env_path)
    spreadsheet_id = (os.environ.get("SPREADSHEET_ID") or "").strip()
    if not spreadsheet_id:
        print(f"SPREADSHEET_ID가 없습니다. {env_path} 파일에 SPREADSHEET_ID=... 값을 추가하세요.")
        raise SystemExit(2)

    credentials_info = load_credentials_info(env_path)
    service_email = credentials_info.get("client_email")
    gspread, Credentials, _api_error = load_google_modules()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(spreadsheet_id)
    return spreadsheet, gspread, service_email


def get_or_create_worksheet(spreadsheet: Any, title: str, rows: int, cols: int) -> Any:
    try:
        return spreadsheet.worksheet(title)
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_headers(worksheet: Any, headers: list[str]) -> None:
    current = worksheet.row_values(1)
    if current[: len(headers)] == headers:
        return
    end_col = chr(ord("A") + len(headers) - 1)
    worksheet.update(range_name=f"A1:{end_col}1", values=[headers])
    try:
        worksheet.freeze(rows=1)
    except Exception:
        pass


def prepared_news_rows(data: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in data.get("items", []):
        summary_ko = str(item.get("summary_ko") or "").strip()
        if not summary_ko:
            continue
        rows.append(
            [
                str(item.get("published_at_kst") or item.get("published_at") or ""),
                str(item.get("category") or ""),
                str(item.get("title") or ""),
                summary_ko,
                str(item.get("link") or ""),
                str(item.get("source") or item.get("feed_title") or ""),
            ]
        )
    return rows


def existing_links(worksheet: Any) -> set[str]:
    values = worksheet.col_values(5)
    return {value.strip() for value in values[1:] if value.strip()}


def append_to_sheets(args: argparse.Namespace) -> int:
    env_path = Path(args.env)
    input_path = Path(args.input)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    news_rows = prepared_news_rows(data)
    service_email_for_errors = service_email_from_env(env_path)

    try:
        spreadsheet, _gspread, service_email = open_spreadsheet(env_path)
        service_email_for_errors = service_email or service_email_for_errors
        used_news = get_or_create_worksheet(spreadsheet, USED_NEWS_SHEET, rows=max(len(news_rows) + 10, 1000), cols=6)
        history = get_or_create_worksheet(spreadsheet, HISTORY_SHEET, rows=1000, cols=3)
        ensure_headers(used_news, USED_NEWS_HEADERS)
        ensure_headers(history, HISTORY_HEADERS)

        skipped = 0
        rows_to_append = news_rows
        if not args.allow_duplicates:
            used_links = existing_links(used_news)
            deduped_rows: list[list[str]] = []
            for row in news_rows:
                link = row[4].strip()
                if link and link in used_links:
                    skipped += 1
                    continue
                deduped_rows.append(row)
            rows_to_append = deduped_rows

        if rows_to_append:
            used_news.append_rows(rows_to_append, value_input_option="USER_ENTERED")

        status = f"완료: {len(rows_to_append)}건 추가"
        if skipped:
            status += f", 중복 {skipped}건 건너뜀"
        if not news_rows:
            status = "요약된 기사 없음"

        sent_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        history.append_row([sent_at, len(rows_to_append), status], value_input_option="USER_ENTERED")

    except Exception as exc:  # noqa: BLE001 - turn Sheets API errors into actionable messages.
        if exc.__class__.__name__ == "APIError" and ("403" in str(exc) or "PERMISSION_DENIED" in str(exc)):
            print(friendly_permission_message(exc, service_email_for_errors))
            return 3
        print(f"Google Sheets 누적 중 오류가 발생했습니다: {type(exc).__name__}: {exc}")
        return 1

    print(f"입력 파일: {input_path}")
    print(f"사용된뉴스 추가: {len(rows_to_append)}건")
    if skipped:
        print(f"중복 건너뜀: {skipped}건")
    print("브리핑히스토리 1행 추가 완료")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="요약/분류된 뉴스 JSON을 Google Sheets에 누적합니다.")
    parser.add_argument("--input", default="outputs/news_recent_24h.json", help="요약/분류된 뉴스 JSON 경로")
    parser.add_argument("--env", default=".env", help="GOOGLE_CREDENTIALS_PATH와 SPREADSHEET_ID를 읽을 .env 경로")
    parser.add_argument("--allow-duplicates", action="store_true", help="이미 같은 원문 링크가 있어도 다시 추가")
    return parser.parse_args()


def main() -> int:
    return append_to_sheets(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
