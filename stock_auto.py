#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장마감후여기 종목공부 자동 생성기
pykrx 데이터 수집 → PIL 카드뉴스 생성 → Groq 글 생성 → git push
실행: python3 stock_auto.py
"""
from __future__ import annotations
import sys, os, json, re, time, requests, subprocess, logging
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from PIL import Image, ImageDraw, ImageFont
from pykrx import stock as krx

sys.path.insert(0, str(Path(__file__).parent))
from config import GROQ_API_KEY

# ── 경로 ────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
PENDING_DIR  = BASE_DIR / "posts" / "pending"
IMAGES_DIR   = BASE_DIR / "posts" / "images"
LOG_DIR      = BASE_DIR / "logs"
HISTORY_FILE = BASE_DIR / "posts" / "stock_history.json"
for _d in [PENDING_DIR, IMAGES_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"stock_{datetime.now().strftime('%Y%m%d_%H')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── 색상 (card_generator.py 다크 테마 통일) ─────
C_BG     = "#0D1117"
C_PANEL  = "#161B22"
C_PANEL2 = "#1C2128"
C_BORDER = "#30363D"
C_UP     = "#FF4757"
C_DOWN   = "#4D96FF"
C_TEXT   = "#E6EDF3"
C_DIM    = "#8B949E"
C_GOLD   = "#FFD43B"
C_GREEN  = "#3FB950"
C_THEME  = "#CC5DE8"

def rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

# ── 폰트 (Linux NotoSansCJK 우선, 폴백 기본) ────
_FONT_CANDIDATES = [
    "/usr/share/fonts/google-noto-cjk/NotoSansCJKkr-Bold.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    # Windows 폴백 (로컬 테스트용)
    r"C:\Windows\Fonts\malgunbd.ttf",
    r"C:\Windows\Fonts\malgun.ttf",
]
_FONT_PATH = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)

def font(bold: bool = False, size: int = 24) -> ImageFont.FreeTypeFont:
    if _FONT_PATH:
        try:
            return ImageFont.truetype(_FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()

def _tw(d: ImageDraw.ImageDraw, text: str, f) -> int:
    try:
        return int(d.textlength(text, font=f))
    except Exception:
        bbox = d.textbbox((0, 0), text, font=f)
        return bbox[2] - bbox[0]

def pill(d, x, y, text, bg, fg, fsize=15, px=14, py=8) -> int:
    f  = font(True, fsize)
    tw = _tw(d, text, f)
    w  = tw + px * 2
    h  = fsize + py * 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=rgb(bg))
    d.text((x + px, y + py), text, font=f, fill=rgb(fg))
    return w

def fmt_val(val: int) -> str:
    sign = "+" if val >= 0 else "-"
    a = abs(val)
    if a >= 10000:
        jo  = a // 10000
        awk = a % 10000
        return f"{sign}{jo}조 {awk:,}억" if awk >= 100 else f"{sign}{jo}조"
    return f"{sign}{a:,}억"

def up_col(val) -> str:
    return C_UP if val >= 0 else C_DOWN

# ── GROQ ─────────────────────────────────────
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── 이력 관리 ────────────────────────────────
HISTORY_KEEP_DAYS = 90

def load_history() -> set:
    if not HISTORY_FILE.exists():
        return set()
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        cutoff = datetime.now() - timedelta(days=HISTORY_KEEP_DAYS)
        return {e["ticker"] for e in data if datetime.fromisoformat(e["ts"]) > cutoff}
    except Exception:
        return set()

def save_history(ticker: str):
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
        cutoff = datetime.now() - timedelta(days=HISTORY_KEEP_DAYS)
        data = [e for e in data if datetime.fromisoformat(e["ts"]) > cutoff]
        data.append({"ticker": ticker, "ts": datetime.now().isoformat()})
        HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"history 저장 실패: {e}")

# ── 종목 풀 (KOSPI 시총 상위 + KOSDAQ 주요) ───
STOCK_POOL = [
    "005930", "000660", "005380", "035420", "000270",
    "068270", "035720", "051910", "006400", "028260",
    "105560", "055550", "066570", "086790", "032830",
    "003550", "012330", "018260", "096770", "034730",
    "030200", "017670", "003490", "009150", "015760",
    "000810", "010130", "011790", "042660", "011780",
    # KOSDAQ
    "247540", "086520", "091990", "328130", "196170",
    "263750", "041510", "122870", "035900", "293490",
]

def pick_stock() -> tuple[str, str] | None:
    history = load_history()
    remaining = [t for t in STOCK_POOL if t not in history]
    if not remaining:
        log.info("전체 종목 순환 완료, 이력 초기화")
        HISTORY_FILE.unlink(missing_ok=True)
        remaining = STOCK_POOL[:]
    ticker = remaining[0]
    try:
        name = krx.get_market_ticker_name(ticker)
        if not name:
            raise ValueError("이름 없음")
        return ticker, name
    except Exception as e:
        log.error(f"종목명 조회 실패 ({ticker}): {e}")
        return None

# ── 데이터 수집 ──────────────────────────────
def collect_stock_data(ticker: str, name: str) -> dict | None:
    today    = datetime.now().strftime("%Y%m%d")
    from_30d = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
    from_52w = (datetime.now() - timedelta(days=380)).strftime("%Y%m%d")

    try:
        # OHLCV
        ohlcv = krx.get_market_ohlcv_by_date(from_30d, today, ticker)
        if ohlcv is None or ohlcv.empty:
            log.error(f"OHLCV 없음: {name}")
            return None
        close      = int(ohlcv.iloc[-1]["종가"])
        prev_close = int(ohlcv.iloc[-2]["종가"]) if len(ohlcv) >= 2 else close
        chg_pct    = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0

        # 52주 고저
        ohlcv_52w = krx.get_market_ohlcv_by_date(from_52w, today, ticker)
        high_52w  = int(ohlcv_52w["고가"].max())  if (ohlcv_52w is not None and not ohlcv_52w.empty) else 0
        low_52w   = int(ohlcv_52w["저가"].min())  if (ohlcv_52w is not None and not ohlcv_52w.empty) else 0

        # 시가총액
        market_cap = 0
        try:
            cap_df = krx.get_market_cap_by_date(from_30d, today, ticker)
            if cap_df is not None and not cap_df.empty:
                market_cap = int(cap_df.iloc[-1]["시가총액"]) // 100_000_000
        except Exception:
            pass

        # 기본 지표
        per = pbr = 0.0
        try:
            fund_df = krx.get_market_fundamental_by_date(from_30d, today, ticker)
            if fund_df is not None and not fund_df.empty:
                row = fund_df.iloc[-1]
                per = round(float(row.get("PER", 0) or 0), 1)
                pbr = round(float(row.get("PBR", 0) or 0), 1)
        except Exception:
            pass

        # 수급 (20일)
        foreign_net = inst_net = retail_net = 0
        try:
            tr = krx.get_market_trading_value_by_date(from_30d, today, ticker)
            if tr is not None and not tr.empty:
                for col_name in ["외국인합계", "외국인"]:
                    if col_name in tr.columns:
                        foreign_net = int(tr[col_name].sum()) // 100_000_000
                        break
                for col_name in ["기관합계", "기관"]:
                    if col_name in tr.columns:
                        inst_net = int(tr[col_name].sum()) // 100_000_000
                        break
                for col_name in ["개인"]:
                    if col_name in tr.columns:
                        retail_net = int(tr[col_name].sum()) // 100_000_000
                        break
        except Exception:
            pass

        # 핵심 데이터 부족 시 스킵 (KRX API 차단 방지)
        if market_cap == 0 and per == 0.0 and foreign_net == 0:
            import logging as _log
            _log.getLogger().error(f"{name}: 시총/PER/수급 모두 0 — KRX API 응답 없음. 글 생성 스킵.")
            return None

        return {
            "ticker":      ticker,
            "name":        name,
            "close":       close,
            "chg_pct":     chg_pct,
            "high_52w":    high_52w,
            "low_52w":     low_52w,
            "market_cap":  market_cap,
            "per":         per,
            "pbr":         pbr,
            "foreign_net": foreign_net,
            "inst_net":    inst_net,
            "retail_net":  retail_net,
            "date_str":    datetime.now().strftime("%Y년 %m월 %d일"),
            "date_short":  datetime.now().strftime("%y/%m/%d"),
        }
    except Exception as e:
        log.error(f"데이터 수집 실패 ({name}): {e}")
        return None

# ── 카드뉴스 생성 ─────────────────────────────
def make_overview_card(D: dict, out_dir: Path) -> Path:
    W, H = 966, 460
    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    col = up_col(D["chg_pct"])
    d.rectangle([0, 0, W, 4], fill=rgb(col))
    d.text((40, 18), D["date_str"], font=font(False, 14), fill=rgb(C_DIM))
    pill(d, 820, 12, "장마감후여기", C_GREEN, C_BG, fsize=12, px=12, py=7)

    d.text((40, 52), D["name"], font=font(True, 46), fill=rgb(C_TEXT))
    cap_txt = f"{D['ticker']}  |  시총 {D['market_cap']:,}억 원" if D["market_cap"] else D["ticker"]
    d.text((42, 112), cap_txt, font=font(False, 16), fill=rgb(C_DIM))

    d.text((40, 146), f"{D['close']:,}원", font=font(True, 58), fill=rgb(C_TEXT))

    chg_bg = "#2D0A0E" if D["chg_pct"] >= 0 else "#0A1828"
    arrow  = "▲" if D["chg_pct"] >= 0 else "▼"
    pill(d, 40, 218, f"{arrow} {abs(D['chg_pct']):.2f}%", chg_bg, col, fsize=16, px=16, py=9)

    d.line([(40, 270), (W - 40, 270)], fill=rgb(C_BORDER), width=1)

    metrics = [
        ("52주 고가", f"{D['high_52w']:,}원" if D["high_52w"] else "-"),
        ("52주 저가", f"{D['low_52w']:,}원"  if D["low_52w"]  else "-"),
        ("PER",      f"{D['per']}배"         if D["per"]      else "-"),
        ("PBR",      f"{D['pbr']}배"         if D["pbr"]      else "-"),
    ]
    col_w = (W - 80) // 4
    for i, (label, val) in enumerate(metrics):
        x = 40 + i * col_w
        d.rounded_rectangle([x, 284, x + col_w - 12, 380], radius=8, fill=rgb(C_PANEL))
        d.text((x + 16, 300), label, font=font(False, 13), fill=rgb(C_DIM))
        d.text((x + 16, 326), val,   font=font(True, 21),  fill=rgb(C_TEXT))

    d.text((40, 432), "정보 제공 목적이며 투자 권유가 아닙니다",
           font=font(False, 11), fill=rgb("#4A5568"))

    p = out_dir / "01_overview.png"
    img.save(p)
    log.info(f"카드 저장: {p.name}")
    return p


def make_supply_card(D: dict, out_dir: Path) -> Path:
    W, H = 900, 320
    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 4], fill=rgb(C_THEME))
    d.text((40, 18), f"{D['name']} — 최근 수급 흐름", font=font(True, 20), fill=rgb(C_TEXT))
    d.text((40, 48), "외인·기관·개인 누가 사고 팔았나 (20거래일 기준)",
           font=font(False, 13), fill=rgb(C_DIM))
    d.line([(40, 72), (W - 40, 72)], fill=rgb(C_BORDER), width=1)

    items   = [("외국인", D["foreign_net"]), ("기관", D["inst_net"]), ("개인", D["retail_net"])]
    max_abs = max((abs(v) for _, v in items), default=1) or 1
    ROW_BGS = ["#180A0C", C_PANEL, "#080E18"]

    for i, (label, val) in enumerate(items):
        ry  = 80 + i * 72
        col = C_UP if val >= 0 else C_DOWN
        d.rectangle([0, ry, W, ry + 72], fill=rgb(ROW_BGS[i]))
        d.text((40, ry + 20), label, font=font(True, 20), fill=rgb(C_TEXT))
        bar_w = max(4, int(abs(val) / max_abs * 340))
        d.rounded_rectangle([156, ry + 30, 156 + bar_w, ry + 40], radius=5, fill=rgb(col))
        d.text((170 + bar_w, ry + 16), fmt_val(val), font=font(True, 24), fill=rgb(col))

    d.text((40, 300), "정보 제공 목적이며 투자 권유가 아닙니다",
           font=font(False, 11), fill=rgb("#4A5568"))

    p = out_dir / "02_supply.png"
    img.save(p)
    log.info(f"카드 저장: {p.name}")
    return p


def generate_cards(D: dict) -> tuple[list[Path], Path]:
    ts      = datetime.now().strftime("%y%m%d")
    out_dir = IMAGES_DIR / ts / D["ticker"]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths   = []
    try:
        paths.append(make_overview_card(D, out_dir))
        paths.append(make_supply_card(D, out_dir))
    except Exception as e:
        log.error(f"카드뉴스 생성 오류: {e}")
    return paths, out_dir


# ── Groq 글 생성 ──────────────────────────────
def generate_post(D: dict) -> str | None:
    prompt = f"""아래 종목 데이터를 바탕으로 네이버 블로그 '장마감후여기'의 종목공부 글을 한국어로 작성하세요.

종목명: {D['name']} ({D['ticker']})
현재가: {D['close']:,}원 ({'+' if D['chg_pct']>=0 else ''}{D['chg_pct']:.2f}%)
시가총액: {D['market_cap']:,}억 원
52주 최고/최저: {D['high_52w']:,}원 / {D['low_52w']:,}원
PER: {D['per']}배 / PBR: {D['pbr']}배
최근 수급(억): 외국인 {'+' if D['foreign_net']>=0 else ''}{D['foreign_net']:,} / 기관 {'+' if D['inst_net']>=0 else ''}{D['inst_net']:,} / 개인 {'+' if D['retail_net']>=0 else ''}{D['retail_net']:,}
기준일: {D['date_str']}

[작성 규칙]
- 닉네임 '장마여'로 1인칭 서술
- 문체: ~했는데요/~거든요(친근) + ~입니다(신뢰) 혼합
- 매수·매도 추천 표현 절대 금지
- 이모티콘 절대 금지
- 소제목 3~4개: ### 형식, 소제목 바로 아래 [이미지] 마커 1개
- 본문 1,500~2,500자
- 면책 마지막에 반드시 포함: "본 글은 정보 제공 및 개인 공부 기록 목적이며, 특정 종목의 매수·매도를 권유하는 것이 아닙니다."

[출력 형식 — 이 형식 그대로, 추가 설명 없이]
제목: {D['name']} 종목공부 — {D['date_str']}
카테고리: 종목공부
이미지키워드: {D['name'].replace(' ', '')} 주가 실적 분석 2026
---
[본문 시작]

#종목공부 #{D['name'].replace(' ','')} #코스피 #주식공부 #종목분석 #재무분석 #수급분석 #장마여 #장마감후여기 #투자일지 #한국주식 #실적분석 #밸류에이션 #주식투자 #오늘뉴스"""

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.3-70b-versatile",
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens":  4096,
            },
            timeout=60,
        )
        result = resp.json()
        if "choices" not in result:
            log.error(f"Groq 오류: {result.get('error', result)}")
            return None
        text = result["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```$",       "", text.strip())
        log.info(f"Groq 생성 완료: {D['name']}")
        return text
    except Exception as e:
        log.error(f"Groq 생성 실패: {e}")
        return None


def save_post(content: str, D: dict) -> Path | None:
    try:
        safe = re.sub(r'[\\/:*?"<>|]', "_", D["name"])[:10]
        ts   = datetime.now().strftime("%y%m%d%H")
        fname = f"{ts}_종목공부_{safe}.txt"
        path  = PENDING_DIR / fname
        path.write_text(content, encoding="utf-8")
        log.info(f"저장: {fname}")
        return path
    except Exception as e:
        log.error(f"저장 실패: {e}")
        return None


def git_push():
    # stock_auto.py 위치가 ~/claude/econ_auto/ 이면 repo root = 한 단계 위
    repo_dir = BASE_DIR
    try:
        subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
        msg = f"stock_auto: {datetime.now().strftime('%Y-%m-%d %H:%M')} 종목공부 자동 생성"
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", msg],
            capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout:
            log.info("커밋할 변경 없음")
            return
        subprocess.run(["git", "-C", str(repo_dir), "push"], check=True)
        log.info("git push 완료")
    except subprocess.CalledProcessError as e:
        log.error(f"git push 실패: {e}")


# ── 메인 ─────────────────────────────────────
def main():
    log.info("===== 종목공부 자동화 시작 =====")

    result = pick_stock()
    if not result:
        log.error("종목 선택 실패")
        sys.exit(1)
    ticker, name = result
    log.info(f"선택 종목: {name} ({ticker})")

    D = collect_stock_data(ticker, name)
    if not D:
        log.error("데이터 수집 실패")
        sys.exit(1)

    img_paths, img_dir = generate_cards(D)
    log.info(f"카드뉴스 {len(img_paths)}개 생성 → {img_dir}")

    content = generate_post(D)
    if not content:
        log.error("글 생성 실패")
        sys.exit(1)

    saved = save_post(content, D)
    if saved:
        save_history(ticker)
        git_push()
        log.info(f"===== 완료: {name} =====")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
