#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
마감 시황 자동 생성기 (Oracle 실행)
pykrx 데이터 수집 → Groq 글 생성 → git push
크론: 0 16 * * 1-5  (KST 16:00, 장 마감 30분 후)
"""
from __future__ import annotations
import sys, os, subprocess, logging
from datetime import date, timedelta
from pathlib import Path

import requests
from pykrx import stock as krx

sys.path.insert(0, str(Path(__file__).parent))
from config import GROQ_API_KEY

# ── 경로 ────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
PENDING_DIR = BASE_DIR / "posts" / "pending"
LOG_DIR     = BASE_DIR / "logs"
for _d in [PENDING_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── 로거 ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "market_auto.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── 업종 지수 티커 ────────────────────────────────
SECTOR_MAP = {
    "1150": "음식료품", "1153": "화학",    "1154": "의약품",
    "1156": "철강금속", "1157": "기계",    "1158": "전기전자",
    "1160": "운수장비", "1161": "유통업",  "1163": "건설업",
    "1165": "통신업",   "1166": "금융업",  "1168": "증권",
    "1170": "서비스업",
}

# ── 거래일 확인 ──────────────────────────────────
def is_trading_day(d: date) -> bool:
    date_str = d.strftime("%Y%m%d")
    try:
        days = krx.get_market_trading_days(date_str, date_str, market="KOSPI")
        return len(days) > 0
    except Exception:
        return d.weekday() < 5  # fallback: 주말만 제외


# ── 데이터 수집 ──────────────────────────────────
def _recent_days(n=7):
    today = date.today()
    return (today - timedelta(days=n)).strftime("%Y%m%d"), today.strftime("%Y%m%d")


def fetch_index(ticker: str, name: str) -> dict:
    from_d, to_d = _recent_days(10)
    try:
        df = krx.get_index_ohlcv_by_date(from_d, to_d, ticker)
        if df is None or df.empty or len(df) < 2:
            return {"name": name, "close": 0.0, "chg": 0.0, "pct": 0.0}
        close = float(df["종가"].iloc[-1])
        prev  = float(df["종가"].iloc[-2])
        chg   = round(close - prev, 2)
        pct   = round(chg / prev * 100, 2) if prev else 0.0
        return {"name": name, "close": close, "chg": chg, "pct": pct}
    except Exception as e:
        log.warning(f"{name} 수집 실패: {e}")
        return {"name": name, "close": 0.0, "chg": 0.0, "pct": 0.0}


def fetch_supply() -> dict:
    from_d, to_d = _recent_days(7)
    fallback = {
        "foreign":   {"val": 0, "label": "외국인"},
        "institute": {"val": 0, "label": "기관"},
        "retail":    {"val": 0, "label": "개인"},
    }
    try:
        df = krx.get_market_trading_value_by_date(from_d, to_d, "KOSPI")
        if df is None or df.empty:
            return fallback
        row = df.iloc[-1]

        def _pick(candidates):
            for c in candidates:
                if c in row.index:
                    return int(round(float(row[c]) / 1e8))
            return 0

        return {
            "foreign":   {"val": _pick(["외국인합계", "외국인", "외국인 합계"]), "label": "외국인"},
            "institute": {"val": _pick(["기관합계",   "기관",   "기관 합계"]),   "label": "기관"},
            "retail":    {"val": _pick(["개인"]),                                "label": "개인"},
        }
    except Exception as e:
        log.warning(f"수급 수집 실패: {e}")
        return fallback


def fetch_sectors(top_n: int = 6) -> list[dict]:
    from_d, to_d = _recent_days(10)
    results = []
    for ticker, name in SECTOR_MAP.items():
        try:
            df = krx.get_index_ohlcv_by_date(from_d, to_d, ticker)
            if df is None or df.empty or len(df) < 2:
                continue
            close = float(df["종가"].iloc[-1])
            prev  = float(df["종가"].iloc[-2])
            pct   = round((close - prev) / prev * 100, 2) if prev else 0.0
            results.append({"name": name, "pct": pct})
        except Exception:
            continue
    if not results:
        return []
    results.sort(key=lambda x: x["pct"])
    n = max(1, top_n // 2)
    return results[:n] + results[-n:]  # 하위 n개 + 상위 n개


# ── Groq 글 생성 ─────────────────────────────────
def generate_post(kospi: dict, kosdaq: dict, supply: dict, sectors: list[dict]) -> str:
    today = date.today()
    if sys.platform == "win32":
        date_str = today.strftime("%Y년 %#m월 %#d일")
    else:
        date_str = today.strftime("%Y년 %-m월 %-d일")

    sign = "+" if kospi["pct"] >= 0 else ""
    sector_str = " / ".join(
        f"{s['name']} {'+' if s['pct']>=0 else ''}{s['pct']}%"
        for s in sorted(sectors, key=lambda x: x["pct"], reverse=True)[:5]
    )
    sup = supply
    sup_str = (
        f"외국인 {sup['foreign']['val']:+,}억 / "
        f"기관 {sup['institute']['val']:+,}억 / "
        f"개인 {sup['retail']['val']:+,}억"
    )

    prompt = f"""네이버 블로그 "장마감후여기" (닉네임: 장마여) 평일 마감 브리핑 글을 작성해줘.

[마감 데이터 — {date_str}]
코스피: {kospi['close']:,.2f} ({sign}{kospi['pct']:.2f}%, {kospi['chg']:+,.2f}p)
코스닥: {kosdaq['close']:,.2f} ({'+' if kosdaq['pct']>=0 else ''}{kosdaq['pct']:.2f}%)
수급: {sup_str}
업종 (상승↑하락↓): {sector_str}

[출력 형식 — 반드시 이 틀 그대로]
제목: 코스피 마감 [{sign}{kospi['pct']:.2f}%] 핵심 한 줄, {date_str}
카테고리: 시황
이미지키워드: 코스피 마감 차트 {today.year}
---
(본문 시작)

첫 단락: 제목 키워드 그대로 1~2문장 반복 (SEO 필수)

▶ 오늘 한눈에
- 코스피 {kospi['close']:,.2f} ({sign}{kospi['pct']:.2f}%), 코스닥 {kosdaq['close']:,.2f} ({'+' if kosdaq['pct']>=0 else ''}{kosdaq['pct']:.2f}%)
- 외인 {sup['foreign']['val']:+,}억 / 기관 {sup['institute']['val']:+,}억 / 개인 {sup['retail']['val']:+,}억
- 주도 섹터: [위 데이터 기반]

> "오늘 시장 감성 코멘트 한 줄"

━━━━━━━━━━━━━━━━━━━━━━━

## 코스피 마감 어땠나 — 오늘 지수 흐름
[이미지]
(2~3문단, ~했는데요 ~거든요 혼합)
→ 장마여 코멘트 1줄

━━━━━━━━━━━━━━━━━━━━━━━

## 수급 흐름 — 외인·기관·개인 누가 움직였나
[이미지]
| 주체 | 순매수(억) | 방향 |
|---|---|---|
| 외국인 | {sup['foreign']['val']:+,} | {'매수 우위' if sup['foreign']['val']>0 else '매도 우위'} |
| 기관 | {sup['institute']['val']:+,} | {'매수 우위' if sup['institute']['val']>0 else '매도 우위'} |
| 개인 | {sup['retail']['val']:+,} | {'매수 우위' if sup['retail']['val']>0 else '매도 우위'} |
→ 장마여 코멘트 1줄

━━━━━━━━━━━━━━━━━━━━━━━

## 오늘 주도 테마는 — 시장을 끌어올린 섹터
[이미지]
(업종 데이터 기반 2문단)
→ 장마여 코멘트 1줄

━━━━━━━━━━━━━━━━━━━━━━━

## 내일 체크포인트 — 우산 챙길까, 접을까?
[이미지]
(다음 거래일 주요 관전 포인트 2~3가지)
→ 장마여 코멘트 1줄

━━━━━━━━━━━━━━━━━━━━━━━

**Q. 오늘 코스피 왜 {'올랐' if kospi['pct']>=0 else '내렸'}나요?**
A. (2~3문장 팩트 답변)

**Q. 오늘 외국인이 가장 많이 {'산' if sup['foreign']['val']>0 else '판'} 섹터는?**
A. (간결 답변)

━━━━━━━━━━━━━━━━━━━━━━━

(마무리 1~2줄, 장마여 말투)
본 글은 정보 제공 목적이며 투자 권유가 아닙니다.

#코스피마감 #코스닥마감 #오늘증시 #증시마감 #한국증시 #코스피종가 #외국인매매동향 #기관매매 #증시브리핑 #장마감 #주식시황 #장마여 #장마감후여기 #투자일지 #한국주식 #실시간뉴스 #오늘뉴스 #핫이슈 #국내증시 #수급분석"""

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 3000,
    }
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers, json=body, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ── 저장 + 푸시 ──────────────────────────────────
def save_post(content: str) -> Path:
    prefix = date.today().strftime("%y%m%d")
    existing = sorted(PENDING_DIR.glob(f"{prefix}*_시황_*.txt"))
    nn = len(existing) + 1
    path = PENDING_DIR / f"{prefix}{nn:02d}_시황_코스피마감브리핑.txt"
    path.write_text(content, encoding="utf-8")
    log.info(f"저장: {path.name}")
    return path


def git_push():
    try:
        subprocess.run(["git", "-C", str(BASE_DIR), "add", "-A"], check=True)
        msg = f"auto: 마감시황 {date.today().strftime('%Y-%m-%d')}"
        result = subprocess.run(
            ["git", "-C", str(BASE_DIR), "commit", "-m", msg],
            capture_output=True, text=True,
        )
        if "nothing to commit" in result.stdout:
            log.info("커밋할 변경사항 없음")
            return
        subprocess.run(["git", "-C", str(BASE_DIR), "push"], check=True)
        log.info("git push 완료")
    except subprocess.CalledProcessError as e:
        log.error(f"git push 실패: {e}")


# ── 메인 ─────────────────────────────────────────
def main():
    today = date.today()
    log.info(f"===== 마감시황 자동화 시작: {today} =====")

    if not is_trading_day(today):
        log.info(f"{today} 휴장일 — 종료")
        return

    log.info("거래일 확인 완료 → 데이터 수집")
    kospi   = fetch_index("1001", "KOSPI")
    kosdaq  = fetch_index("2001", "KOSDAQ")
    supply  = fetch_supply()
    sectors = fetch_sectors(top_n=6)

    log.info(f"코스피: {kospi['close']:,.2f} ({kospi['pct']:+.2f}%)")
    log.info(f"코스닥: {kosdaq['close']:,.2f} ({kosdaq['pct']:+.2f}%)")
    log.info(f"수급 — 외인: {supply['foreign']['val']:+,}억 / 기관: {supply['institute']['val']:+,}억")
    log.info(f"섹터: {sectors}")

    log.info("Groq 글 생성 중...")
    content = generate_post(kospi, kosdaq, supply, sectors)
    log.info(f"글 생성 완료 ({len(content)}자)")

    save_post(content)
    git_push()
    log.info("===== 완료 =====")


if __name__ == "__main__":
    main()
