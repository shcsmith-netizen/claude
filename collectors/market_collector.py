# coding: utf-8
"""
pykrx + yfinance + 네이버 금융 테마 로 마감 데이터 자동 수집
반환값: dict (card_generator.py 의 DATA 형식과 동일)
"""

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from pykrx import stock
import yfinance as yf
import requests
import re

# 섹터 티커 → 한국어 이름 매핑 (pykrx KOSPI 업종 지수)
SECTOR_MAP = {
    "1150": "음식료품",
    "1153": "화학",
    "1154": "의약품",
    "1156": "철강금속",
    "1157": "기계",
    "1158": "전기전자",
    "1163": "건설업",
    "1166": "금융업",
    "1168": "증권",
    "1160": "운수장비",
    "1161": "유통업",
    "1165": "통신업",
    "1170": "서비스업",
}

# 한국 증시 휴장일 (주말 제외 특수 공휴일 + 대체공휴일)
# KRX 공식 발표 기준으로 연간 업데이트 필요
_KR_MARKET_HOLIDAYS = {
    # 2025
    date(2025, 1,  1),
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),  # 설날 연휴
    date(2025, 3,  3),  # 삼일절 대체 (3/1=토)
    date(2025, 5,  1),  # 근로자의 날
    date(2025, 5,  5),  # 어린이날
    date(2025, 5,  6),  # 부처님오신날
    date(2025, 6,  6),  # 현충일
    date(2025, 8, 15),  # 광복절
    date(2025, 10, 3),  # 개천절
    date(2025, 10, 5), date(2025, 10, 6), date(2025, 10, 7),  # 추석 연휴
    date(2025, 10, 9),  # 한글날
    date(2025, 12, 25), # 크리스마스
    # 2026
    date(2026, 1,  1),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),  # 설날 연휴
    date(2026, 3,  2),  # 삼일절 대체 (3/1=일)
    date(2026, 5,  1),  # 근로자의 날
    date(2026, 5,  5),  # 어린이날
    date(2026, 5, 25),  # 부처님오신날 (음력 4/8 예상)
    date(2026, 6,  6),  # 현충일 (토→대체: 6/8)
    date(2026, 6,  8),  # 현충일 대체
    date(2026, 8, 17),  # 광복절 대체 (8/15=토)
    date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1),  # 추석 연휴 (예상)
    date(2026, 10, 3),  # 개천절 (토→대체: 10/5?)
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 크리스마스
    # 2027 (기본 고정 공휴일만)
    date(2027, 1,  1),
    date(2027, 3,  1),
    date(2027, 5,  1),
    date(2027, 5,  5),
    date(2027, 6,  6),
    date(2027, 8, 15),
    date(2027, 10, 3),
    date(2027, 10, 9),
    date(2027, 12, 25),
}


def _is_market_holiday(d):
    """해당 날짜가 증시 휴장일인지 (주말 포함)"""
    return d.weekday() >= 5 or d in _KR_MARKET_HOLIDAYS


def _next_trading_day_after(today):
    """오늘 다음 첫 번째 거래일 반환 (공휴일 자동 제외)"""
    d = today + timedelta(days=1)
    for _ in range(30):
        if not _is_market_holiday(d):
            return d
        d += timedelta(days=1)
    return d


def _last_n_trading_days(n=5):
    """오늘 포함 최근 n일치 날짜 범위 반환 (YYYYMMDD)"""
    today = date.today()
    from_d = (today - timedelta(days=n * 2)).strftime("%Y%m%d")
    to_d   = today.strftime("%Y%m%d")
    return from_d, to_d


def _pct_change(df, col="종가"):
    """마지막 2개 행으로 등락률 계산"""
    if len(df) < 2:
        return 0.0
    prev  = df[col].iloc[-2]
    close = df[col].iloc[-1]
    return round((close - prev) / prev * 100, 2) if prev else 0.0


def fetch_index(ticker, name):
    """지수 마감 수치 반환 {close, chg, pct}"""
    from_d, to_d = _last_n_trading_days(5)
    try:
        df = stock.get_index_ohlcv_by_date(from_d, to_d, ticker)
    except Exception as e:
        print(f"[{name} 수집 오류] {e}")
        return {"close": 0.0, "chg": 0.0, "pct": 0.0}
    if df is None or df.empty or len(df) < 1:
        return {"close": 0.0, "chg": 0.0, "pct": 0.0}
    try:
        close = float(df["종가"].iloc[-1])
        prev  = float(df["종가"].iloc[-2]) if len(df) >= 2 else close
        chg   = round(close - prev, 2)
        pct   = round(chg / prev * 100, 2) if prev else 0.0
        return {"close": close, "chg": chg, "pct": pct}
    except Exception as e:
        print(f"[{name} 파싱 오류] {e}")
        return {"close": 0.0, "chg": 0.0, "pct": 0.0}


def fetch_supply():
    """외인/기관/개인 순매수 (KOSPI 기준, 억원)"""
    today  = date.today().strftime("%Y%m%d")
    from_d = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
    try:
        df = stock.get_market_trading_value_by_date(from_d, today, "KOSPI")
        if df.empty:
            return None
        row = df.iloc[-1]
        def _get_col(candidates):
            for c in candidates:
                if c in row.index:
                    return int(round(row[c] / 1e8))
            return 0
        foreign = _get_col(["외국인합계", "외국인", "외국인 합계"])
        inst    = _get_col(["기관합계", "기관", "기관 합계"])
        retail  = _get_col(["개인"])
        return {
            "foreign":   {"val": foreign, "label": "외국인"},
            "institute": {"val": inst,    "label": "기관"},
            "retail":    {"val": retail,  "label": "개인"},
        }
    except Exception as e:
        print(f"[수급 수집 오류] {e}")
        return None


def fetch_sectors(top_n=6):
    """업종별 등락률 상위/하위 (top_n개)"""
    from_d, to_d = _last_n_trading_days(5)
    results = []
    for ticker, name in SECTOR_MAP.items():
        try:
            df = stock.get_index_ohlcv_by_date(from_d, to_d, ticker)
            if df.empty or len(df) < 2:
                continue
            pct = _pct_change(df)
            results.append({"name": name, "pct": pct})
        except Exception:
            continue
    if not results:
        return []
    results.sort(key=lambda x: x["pct"])
    n = max(1, top_n // 2)
    return results[:n] + results[-n:]


def fetch_themes_naver(top_n=8):
    """네이버 금융 테마 일별 등락률 상위/하위 스크래핑"""
    url = "https://finance.naver.com/sise/theme.naver"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "euc-kr"
        text = resp.text

        results, seen = [], set()
        for block in re.findall(r'<tr[^>]*>.*?</tr>', text, re.DOTALL):
            nm = re.search(r'themeCode=\d+[^>]*>([^<]+)</a>', block)
            if not nm:
                continue
            name = nm.group(1).strip()
            if name in seen or len(name) < 2:
                continue
            # 부호 있는 등락률 (첫 번째 +X.XX 또는 −X.XX)
            rates = re.findall(r'([+\-−]\d+\.\d+)', block)
            if not rates:
                continue
            try:
                pct_str = rates[0].replace('−', '-')
                pct = float(pct_str)
            except Exception:
                continue
            if abs(pct) > 30:  # 비정상 수치 필터
                continue
            seen.add(name)
            results.append({"name": name, "pct": pct})

        if not results:
            return []
        results.sort(key=lambda x: x["pct"])
        n = max(1, top_n // 2)
        return results[:n] + results[-n:]
    except Exception as e:
        print(f"[테마 수집 오류] {e}")
        return []


def fetch_fx_oil():
    """달러/원 환율 + 브렌트유 (yfinance)"""
    result = {
        "usdkrw": {"close": 0.0, "chg": 0.0, "pct": 0.0},
        "brent":  {"close": 0.0, "chg": 0.0, "pct": 0.0},
    }
    try:
        fx = yf.download("KRW=X", period="5d", auto_adjust=True, progress=False)
        if not fx.empty and len(fx) >= 2:
            c = float(fx["Close"].iloc[-1])
            p = float(fx["Close"].iloc[-2])
            result["usdkrw"] = {"close": round(c,2), "chg": round(c-p,2), "pct": round((c-p)/p*100,2)}
    except Exception as e:
        print(f"[환율 수집 오류] {e}")
    try:
        br = yf.download("BZ=F", period="5d", auto_adjust=True, progress=False)
        if not br.empty and len(br) >= 2:
            c = float(br["Close"].iloc[-1])
            p = float(br["Close"].iloc[-2])
            result["brent"] = {"close": round(c,2), "chg": round(c-p,2), "pct": round((c-p)/p*100,2)}
    except Exception as e:
        print(f"[유가 수집 오류] {e}")
    return result


def _load_override(date_str_yymmdd):
    """data_override/YYMMDD.json 파일이 있으면 로드해서 반환"""
    import json
    override_path = Path(__file__).parent.parent / "data_override" / f"{date_str_yymmdd}.json"
    if override_path.exists():
        with open(override_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def collect_all(date_yymmdd=None):
    """전체 데이터 수집 → card_generator DATA 형식으로 반환"""
    today  = date.today()
    yymmdd = date_yymmdd or today.strftime("%y%m%d")

    override = _load_override(yymmdd)
    if override:
        print(f"[오버라이드] data_override/{yymmdd}.json 사용")
        return override

    print("[1/4] 지수 수집 중...")
    kospi  = fetch_index("1001", "KOSPI")
    kosdaq = fetch_index("2001", "KOSDAQ")

    print("[2/4] 수급 수집 중...")
    supply = fetch_supply()
    if supply is None:
        supply = {
            "foreign":   {"val": 0, "label": "외국인"},
            "institute": {"val": 0, "label": "기관"},
            "retail":    {"val": 0, "label": "개인"},
        }

    print("[3/4] 섹터·테마 수집 중...")
    sectors = fetch_sectors(top_n=6)
    themes  = fetch_themes_naver(top_n=6)
    for t in themes:
        sectors.append({"name": f"★{t['name']}", "pct": t["pct"]})
    sectors.sort(key=lambda x: x["pct"])

    print("[4/4] 환율·유가 수집 중...")
    global_data = fetch_fx_oil()

    return {
        "date_str":   today.strftime("%Y년 %-m월 %-d일") if sys.platform != "win32" else today.strftime("%Y년 %#m월 %#d일"),
        "date_short": today.strftime("%m.%d (%a)").replace("Mon","월").replace("Tue","화").replace("Wed","수").replace("Thu","목").replace("Fri","금").replace("Sat","토").replace("Sun","일"),
        "headline":   _make_headline(kospi, kosdaq, supply, sectors),
        "kospi":      kospi,
        "kosdaq":     kosdaq,
        "usdkrw":     global_data["usdkrw"],
        "brent":      global_data["brent"],
        "supply":     supply,
        "sectors":    sectors,
        "checkpoint": _make_checkpoint(today, kospi),
    }


def _make_headline(kospi, kosdaq, supply, sectors):
    """지수·수급 기반 자동 헤드라인 생성"""
    direction = "상승" if kospi["pct"] >= 0 else "하락"
    pct_abs   = abs(kospi["pct"])

    foreign = supply["foreign"]["val"]
    if abs(foreign) >= 5000:
        actor = f"외인 {abs(foreign):,}억 {'순매수' if foreign > 0 else '순매도'}"
    else:
        actor = f"{pct_abs:.2f}% {direction}"

    if sectors:
        extremes = sorted(sectors, key=lambda x: abs(x["pct"]), reverse=True)
        top_s = extremes[0]
        name  = top_s["name"].lstrip("★")
        sector_note = f"{name} {'+' if top_s['pct'] >= 0 else ''}{top_s['pct']:.2f}%"
    else:
        sector_note = ""

    return f"{actor} / {sector_note}" if sector_note else actor


def _make_checkpoint(today, kospi):
    """다음 거래일 체크포인트 — 공휴일·연휴 인식"""
    next_day = _next_trading_day_after(today)
    gap      = (next_day - today).days  # 1=내일, 2+=연휴
    nw       = next_day.weekday()       # 0=월, 4=금

    items = []

    if gap >= 3:
        items.append(f"연휴 종료 {next_day.month}/{next_day.day} 갭 방향 확인")
        items.append("연휴 글로벌 이슈 모니터링")
    elif gap == 2:
        items.append(f"{next_day.month}/{next_day.day} 개장 갭 방향 확인")

    if nw == 4:
        items.append("미국 주요 지표 발표 일정 확인")
    elif nw == 0 and gap == 1:
        items.append("월요일 수급 패턴 확인")

    if abs(kospi["pct"]) >= 1.5:
        if kospi["pct"] < 0:
            lvl = (int(kospi["close"]) // 100) * 100
            items.append(f"코스피 {lvl:,} 재탈환 여부")
        else:
            lvl = (int(kospi["close"]) // 100 + 1) * 100
            items.append(f"코스피 {lvl:,} 저항선 확인")

    items.append("외인 수급 연속성 확인")
    return items[:4]


if __name__ == "__main__":
    data = collect_all()
    print("\n=== 수집 완료 ===")
    print(f"코스피: {data['kospi']['close']:,} ({data['kospi']['pct']:+.2f}%)")
    print(f"코스닥: {data['kosdaq']['close']:,} ({data['kosdaq']['pct']:+.2f}%)")
    print(f"환율: {data['usdkrw']['close']:,} ({data['usdkrw']['pct']:+.2f}%)")
    print(f"브렌트유: ${data['brent']['close']:.2f} ({data['brent']['pct']:+.2f}%)")
    print(f"수급 — 외인: {data['supply']['foreign']['val']:+,}억 / 기관: {data['supply']['institute']['val']:+,}억 / 개인: {data['supply']['retail']['val']:+,}억")
    print(f"섹터+테마 ({len(data['sectors'])}개): {data['sectors']}")
    print(f"체크포인트: {data['checkpoint']}")
