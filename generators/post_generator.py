# coding: utf-8
"""
시황 데이터 → jmb 형식 블로그 포스트 자동 생성
Groq API (llama-3.3-70b-versatile) 사용
"""

import sys
import re
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).parent.parent
PENDING_DIR = BASE_DIR / "posts" / "pending"

import sys as _sys
_sys.path.insert(0, str(BASE_DIR))
try:
    from econ_config import GROQ_API_KEY
except ImportError:
    import os
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"


def _build_prompt(data: dict) -> str:
    d = data
    date_str = d.get("date_str", "")
    kospi = d.get("kospi", {})
    kosdaq = d.get("kosdaq", {})
    supply = d.get("supply", {})
    sectors = d.get("sectors", [])
    checkpoint = d.get("checkpoint", {})
    usdkrw = d.get("usdkrw")
    brent = d.get("brent")

    kospi_dir = "올랐" if kospi.get("change_pct", 0) >= 0 else "내렸"
    sign_k = "+" if kospi.get("change_pct", 0) >= 0 else ""
    sign_q = "+" if kosdaq.get("change_pct", 0) >= 0 else ""

    supply_rows = ""
    for k, label in [("foreign", "외국인"), ("institution", "기관"), ("individual", "개인"), ("program", "프로그램")]:
        val = supply.get(k)
        if val is not None:
            sign = "+" if val >= 0 else ""
            direction = "매수 우위" if val >= 0 else "매도 우위"
            supply_rows += f"| {label} | {sign}{int(val):,} | {direction} |\n"

    top_buy = supply.get("top_foreign_buy", [])
    top_buy_text = ""
    for item in top_buy[:3]:
        sign = "+" if item.get("val", 0) >= 0 else ""
        top_buy_text += f"  - {item['name']}: {sign}{int(item['val']):,}억\n"

    sector_text = ""
    for s in sectors[:5]:
        sign = "+" if s.get("change_pct", 0) >= 0 else ""
        sector_text += f"  - {s['name']}: {sign}{s['change_pct']:.2f}%\n"

    cp_items = checkpoint.get("items", [])
    cp_text = "\n".join(f"  - {item}" for item in cp_items[:4])
    next_date = checkpoint.get("next_date", "다음 거래일")

    usdkrw_text = f"환율(USD/KRW): {usdkrw:.1f}원" if usdkrw else ""
    brent_text = f"브렌트유: ${brent:.1f}" if brent else ""

    prompt = f"""너는 '장마여'라는 닉네임으로 운영되는 네이버 블로그 '장마감후여기'의 글 작성자야.
블로그 정체성: 매매 추천 없이 "오늘 시장이 어땠고 왜 이랬는가"를 정리하는 포지션.
타겟: 30~50대 직장인 투자자. 문체: 친근하면서 신뢰감 있는 동네 형/누나 느낌.

오늘({date_str}) 코스피 마감 브리핑 글을 아래 형식으로 작성해줘.

=== 오늘 데이터 ===
코스피: {kospi.get('close', 0):,.2f} ({sign_k}{kospi.get('change_pct', 0):.2f}%, {sign_k}{kospi.get('change_pt', 0):.2f}p)
코스닥: {kosdaq.get('close', 0):,.2f} ({sign_q}{kosdaq.get('change_pct', 0):.2f}%, {sign_q}{kosdaq.get('change_pt', 0):.2f}p)
{usdkrw_text}
{brent_text}

수급:
{supply_rows}
외인 상위 순매수:
{top_buy_text}
섹터:
{sector_text}
내일 체크포인트({next_date}):
{cp_text}
=== 끝 ===

=== 작성 형식 (반드시 이 구조 그대로) ===

제목: 코스피 마감 [{sign_k}{kospi.get('change_pct',0):.2f}%] <핵심 한 줄>, {date_str}
카테고리: 시황
이미지키워드: 코스피 마감 차트 {date_str}
---
<첫 단락: 제목을 자연스럽게 풀어서 1~2문장, ~했습니다 말투>

▶ 오늘 한눈에
- 코스피 <값> (<등락>)
- 코스닥 <값> (<등락>)
- 외인 <값> / 기관 <값> / 개인 <값>
- 주도 섹터: <섹터> / 부진: <섹터>

> "<오늘 시장 한 줄 감성 코멘트>" — 장마여

━━━━━━━━━━━━━━━━━━━━━━━

## 코스피 마감 어땠나 — <소제목>
[이미지]
<2~3문단 본문, ~했는데요 ~거든요 혼합>
→ <개인 코멘트 1줄>

━━━━━━━━━━━━━━━━━━━━━━━

## 수급 흐름 — <소제목>
[이미지]

| 주체 | 순매수(억) | 방향 |
|---|---|---|
{supply_rows}
<외인/기관 주요 종목 설명 2~3문단>
→ <개인 코멘트 1줄>

━━━━━━━━━━━━━━━━━━━━━━━

## 오늘 주도 테마는 — <소제목>
[이미지]
<섹터·종목 설명 2~3문단>
→ <개인 코멘트 1줄>

━━━━━━━━━━━━━━━━━━━━━━━

## 내일 체크포인트 — <소제목>
[이미지]
<다음 거래일 변수 설명>
→ <개인 코멘트 1줄>

━━━━━━━━━━━━━━━━━━━━━━━

**Q. 오늘 코스피 왜 {kospi_dir}나요?**
A. <2~3문장 팩트 답변>

**Q. 오늘 외국인이 가장 많이 산 종목은?**
A. <간결 답변>

━━━━━━━━━━━━━━━━━━━━━━━

<마무리 1~2줄, 부드럽게>
본 글은 정보 제공 목적이며 투자 권유가 아닙니다.

#코스피마감 #코스닥마감 #오늘증시 #증시마감 #한국증시 #코스피종가
#외국인매매동향 #기관매매 #반도체주 #2차전지주 #실적시즌
#증시브리핑 #장마감 #주식시황 #장마여 #장마감후여기
#투자일지 #한국주식 #실시간뉴스 #오늘뉴스 #핫이슈 #국내증시

주의사항:
- 수치는 위 데이터 그대로 사용 (절대 임의로 바꾸지 말 것)
- [이미지] 태그는 각 섹션 ## 소제목 바로 아래에 반드시 유지
- 추천/권유 표현 금지
- 면책 조항("본 글은 정보 제공 목적이며 투자 권유가 아닙니다.") 반드시 포함
- 전체 본문 1,500~2,500자 (태그 제외)
"""
    return prompt


def generate(data: dict, date_yymmdd: str = None) -> Path:
    """시황 데이터를 받아 포스트 파일을 생성하고 경로 반환"""
    import urllib.request
    import json

    if date_yymmdd is None:
        date_yymmdd = datetime.now().strftime("%y%m%d")

    prompt = _build_prompt(data)

    # Groq API 호출
    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    print("[post_generator] Groq API 호출 중...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"].strip()

    # 파일명 결정: 기존 파일 번호 충돌 방지
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    for nn in ("01", "02", "03", "04", "05"):
        fname = PENDING_DIR / f"{date_yymmdd}{nn}_시황_자동생성.txt"
        if not fname.exists():
            break

    fname.write_text(content, encoding="utf-8")
    print(f"[post_generator] 저장 완료: {fname.name}")
    return fname


if __name__ == "__main__":
    # 단독 테스트용
    sys.path.insert(0, str(BASE_DIR))
    from collectors.market_collector import collect_all
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    data = collect_all(date_arg)
    out = generate(data, date_arg)
    print(f"생성된 파일: {out}")
