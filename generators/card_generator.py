# coding: utf-8
"""
장마감후여기 카드 이미지 자동 생성기 v3 (다크 테마)
피그마 디자인 기준: 장마감후여기 카드 디자인 (0jsQpfMNz5iXwx9D3U0ysG)

Usage: py generators/card_generator.py [YYMMDD]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pathlib import Path
from datetime import date
from PIL import Image, ImageDraw, ImageFont
from collectors.market_collector import collect_all

# ── 색상 팔레트 (다크 테마) ─────────────────────
C_BG     = "#0D1117"
C_PANEL  = "#161B22"
C_PANEL2 = "#1C2128"
C_BORDER = "#30363D"
C_UP     = "#FF4757"   # 상승 빨강
C_DOWN   = "#4D96FF"   # 하락 파랑
C_THEME  = "#CC5DE8"   # 테마 보라
C_TEXT   = "#E6EDF3"   # 기본 텍스트
C_DIM    = "#8B949E"   # 흐린 텍스트
C_GOLD   = "#FFD43B"   # 골드
C_GREEN  = "#3FB950"   # 브랜드 뱃지
C_ORANGE = "#FF6348"   # 체크포인트 1번
C_MINT   = "#63E6BE"   # 체크포인트 4번
UP_DIM   = "#2D0A0E"   # 상승 배지 배경
DOWN_DIM = "#0A1828"   # 하락 배지 배경

CHECKPOINT_BADGE_COLORS = [C_ORANGE, C_GOLD, C_DOWN, C_MINT]

def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

FONT_B = r"C:\Windows\Fonts\malgunbd.ttf"
FONT_R = r"C:\Windows\Fonts\malgun.ttf"

def font(bold=False, size=24):
    try:
        return ImageFont.truetype(FONT_B if bold else FONT_R, size)
    except Exception:
        return ImageFont.load_default()

def _tw(d, text, f):
    try:
        return int(d.textlength(text, font=f))
    except Exception:
        bbox = d.textbbox((0, 0), text, font=f)
        return bbox[2] - bbox[0]

def fmt_supply(val):
    """억 단위 → 조/억 혼합 표시  예) -14580 → '-1조 4,580억'"""
    sign = "+" if val >= 0 else "-"
    a = abs(val)
    if a >= 10000:
        jo  = a // 10000
        awk = a % 10000
        return f"{sign}{jo}조 {awk:,}억" if awk >= 100 else f"{sign}{jo}조"
    return f"{sign}{a:,}억"

def pct_arrow(val):
    """▲/▼ + 소수점 2자리 %"""
    return ("▲ " if val >= 0 else "▼ ") + f"{abs(val):.2f}%"

def pill(d, x, y, text, bg, fg, fsize=15, px=14, py=8):
    """다크 pill 배지 그리기, 배지 너비 반환"""
    f  = font(True, fsize)
    tw = _tw(d, text, f)
    w  = tw + px * 2
    h  = fsize + py * 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=rgb(bg))
    d.text((x + px, y + py), text, font=f, fill=rgb(fg))
    return w

def up_down_colors(val):
    col = C_UP if val >= 0 else C_DOWN
    bg  = UP_DIM if val >= 0 else DOWN_DIM
    return col, bg

# ── 출력 경로 ──────────────────────────────────
arg_date = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%y%m%d")
BASE = Path(r"C:\Users\l\scripts\econ_auto")
OUT  = BASE / "posts" / "images" / arg_date
OUT.mkdir(parents=True, exist_ok=True)

print("데이터 수집 중...")
D = collect_all(date_yymmdd=arg_date)
print("수집 완료. 이미지 생성 시작.\n")


# ══════════════════════════════════════════════
# 1. 썸네일 (966 × 520)
# ══════════════════════════════════════════════
def make_thumbnail():
    W, H = 966, 520
    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    # 상단 강조 바 (파랑 4px)
    d.rectangle([0, 0, W, 4], fill=rgb(C_DOWN))

    # 날짜 (왼쪽)
    d.text((40, 26), D["date_str"], font=font(False, 15), fill=rgb(C_DIM))

    # 브랜드 pill (오른쪽)
    pill(d, 840, 18, "장마감후여기", C_GREEN, C_BG, fsize=12, px=12, py=8)

    # ── KOSPI ──────────────────────────────────
    d.text((60, 78), "KOSPI", font=font(True, 16), fill=rgb(C_DIM))
    d.text((60, 100), f"{D['kospi']['close']:,.2f}", font=font(True, 76), fill=rgb(C_TEXT))

    col_k, bg_k = up_down_colors(D["kospi"]["pct"])
    pill(d, 60, 190, pct_arrow(D["kospi"]["pct"]), bg_k, col_k, fsize=15, px=16, py=8)

    chg_k = D["kospi"]["chg"]
    d.text((60, 238), ("-" if chg_k < 0 else "+") + f"{abs(chg_k):.2f}p",
           font=font(False, 14), fill=rgb(C_DIM))

    # ── KOSDAQ ─────────────────────────────────
    d.text((556, 78), "KOSDAQ", font=font(True, 16), fill=rgb(C_DIM))
    d.text((556, 100), f"{D['kosdaq']['close']:,.2f}", font=font(True, 76), fill=rgb(C_TEXT))

    col_q, bg_q = up_down_colors(D["kosdaq"]["pct"])
    pill(d, 556, 190, pct_arrow(D["kosdaq"]["pct"]), bg_q, col_q, fsize=15, px=16, py=8)

    chg_q = D["kosdaq"]["chg"]
    d.text((556, 238), ("-" if chg_q < 0 else "+") + f"{abs(chg_q):.2f}p",
           font=font(False, 14), fill=rgb(C_DIM))

    # ── 구분선 ─────────────────────────────────
    d.line([(60, 288), (W - 60, 288)], fill=rgb(C_BORDER), width=1)

    # ── 달러/원 ────────────────────────────────
    d.text((60, 308), "달러/원", font=font(False, 13), fill=rgb(C_DIM))
    d.text((60, 328), f"{D['usdkrw']['close']:,.2f}", font=font(True, 28), fill=rgb(C_TEXT))
    fx_col, fx_bg = up_down_colors(D["usdkrw"]["pct"])
    pill(d, 60, 368, pct_arrow(D["usdkrw"]["pct"]), fx_bg, fx_col, fsize=12, px=10, py=6)

    # ── 브렌트유 ───────────────────────────────
    d.text((248, 308), "브렌트유", font=font(False, 13), fill=rgb(C_DIM))
    d.text((248, 328), f"${D['brent']['close']:.2f}", font=font(True, 28), fill=rgb(C_TEXT))
    br_col, br_bg = up_down_colors(D["brent"]["pct"])
    pill(d, 248, 368, pct_arrow(D["brent"]["pct"]), br_bg, br_col, fsize=12, px=10, py=6)

    # ── 세로 구분선 ────────────────────────────
    d.line([(450, 304), (450, 400)], fill=rgb(C_BORDER), width=1)

    # ── 수급 미니 (외국인·기관·개인) ───────────
    supply_items = list(D["supply"].values())  # [foreign, institute, retail]
    sx = [470, 656, 800]
    for i, item in enumerate(supply_items):
        col, _ = up_down_colors(item["val"])
        d.text((sx[i], 308), item["label"], font=font(False, 13), fill=rgb(C_DIM))
        d.text((sx[i], 330), fmt_supply(item["val"]), font=font(True, 17), fill=rgb(col))

    # ── 헤드라인 박스 ──────────────────────────
    d.rounded_rectangle([60, 414, W - 60, 470], radius=10, fill=rgb(C_PANEL))
    d.text((78, 434), D["headline"], font=font(False, 14), fill=rgb(C_DIM))

    # 면책
    d.text((60, 496), "정보 제공 목적이며 투자 권유가 아닙니다",
           font=font(False, 11), fill=rgb("#4A5568"))

    p = OUT / "01_thumbnail.png"
    img.save(p)
    print(f"✓ 썸네일      : {p.name}")


# ══════════════════════════════════════════════
# 2. 수급 카드 (900 × 400)
# ══════════════════════════════════════════════
def make_supply():
    W, H = 900, 400
    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    # 상단 강조 바 (외인 방향에 따라 색 변)
    top_col = C_UP if D["supply"]["foreign"]["val"] < 0 else C_DOWN
    d.rectangle([0, 0, W, 4], fill=rgb(top_col))

    # 헤더
    d.text((40, 22), "수급 흐름", font=font(True, 22), fill=rgb(C_TEXT))
    d.text((40, 52), "외인·기관·개인 누가 움직였나", font=font(False, 14), fill=rgb(C_DIM))
    df = font(False, 13)
    dtw = _tw(d, D["date_short"], df)
    d.text((W - 40 - dtw, 28), D["date_short"], font=df, fill=rgb(C_DIM))

    # 구분선
    d.line([(40, 74), (W - 40, 74)], fill=rgb(C_BORDER), width=1)

    supply_items = list(D["supply"].values())
    max_abs = max(abs(item["val"]) for item in supply_items) or 1
    MAX_BAR = 340
    BAR_X   = 156
    ROW_H   = 88
    ROW_BG  = ["#180A0C", C_PANEL, "#080E18"]  # foreign / institute / retail

    for i, item in enumerate(supply_items):
        ry  = 80 + i * ROW_H
        val = item["val"]
        col, _ = up_down_colors(val)

        d.rectangle([0, ry, W, ry + ROW_H], fill=rgb(ROW_BG[i]))
        d.text((40, ry + 20), item["label"], font=font(True, 20), fill=rgb(C_TEXT))

        # 비례 바 차트
        bar_w = max(4, int(abs(val) / max_abs * MAX_BAR))
        d.rounded_rectangle([BAR_X, ry + 28, BAR_X + bar_w, ry + 38],
                            radius=5, fill=rgb(col))

        # 금액 텍스트
        val_str  = fmt_supply(val)
        amount_x = BAR_X + bar_w + 16
        vf       = font(True, 26)
        d.text((amount_x, ry + 16), val_str, font=vf, fill=rgb(col))

        # 방향 레이블
        if val < -10000:
            dir_label = "대규모 순매도"
        elif val < 0:
            dir_label = "순매도"
        else:
            dir_label = "순매수"
        vtw = _tw(d, val_str, vf)
        d.text((amount_x + vtw + 16, ry + 22), dir_label,
               font=font(False, 13), fill=rgb(col))

    # 하단 요약 노트
    d.rectangle([0, 350, W, H], fill=rgb(C_PANEL2))
    fv = D["supply"]["foreign"]["val"]
    iv = D["supply"]["institute"]["val"]
    rv = D["supply"]["retail"]["val"]
    net = iv + rv
    if fv < 0 and net > 0:
        note = f"→ 외인 {fmt_supply(fv)} vs 개인+기관 {fmt_supply(net)} — 지수 낙폭 방어"
    elif fv > 0 and net < 0:
        note = f"→ 외인 {fmt_supply(fv)} 순매수 vs 개인+기관 {fmt_supply(net)}"
    elif fv > 0 and net > 0:
        note = f"→ 외인+개인+기관 동반 매수 — 강한 상승 동력"
    else:
        note = f"→ 외인 {fmt_supply(fv)} / 기관 {fmt_supply(iv)} / 개인 {fmt_supply(rv)}"
    d.text((40, 364), note, font=font(False, 13), fill=rgb(C_DIM))

    p = OUT / "02_supply_card.png"
    img.save(p)
    print(f"✓ 수급 카드   : {p.name}")


# ══════════════════════════════════════════════
# 3. 섹터·테마 카드 (900 × auto)
# ══════════════════════════════════════════════
def make_sector():
    sectors = sorted(D["sectors"], key=lambda x: x["pct"], reverse=True)
    n = len(sectors)
    RH, HH, FH = 68, 88, 48
    H = max(440, HH + n * RH + FH)
    W = 900

    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    # 상단 강조 바 (보라)
    d.rectangle([0, 0, W, 4], fill=rgb(C_THEME))

    d.text((40, 20), "섹터 · 테마 성과", font=font(True, 22), fill=rgb(C_TEXT))

    df  = font(False, 13)
    dtw = _tw(d, D["date_short"], df)
    d.text((W - 40 - dtw, 26), D["date_short"], font=df, fill=rgb(C_DIM))

    # 범례
    d.ellipse([554, 30, 564, 40], fill=rgb(C_DOWN))
    d.text((570, 28), "업종", font=font(False, 12), fill=rgb(C_DIM))
    d.ellipse([614, 30, 624, 40], fill=rgb(C_THEME))
    d.text((630, 28), "★테마", font=font(False, 12), fill=rgb(C_DIM))

    d.line([(40, 66), (W - 40, 66)], fill=rgb(C_BORDER), width=1)

    for i, s in enumerate(sectors):
        y        = HH + i * RH
        is_theme = s["name"].startswith("★")

        d.rectangle([0, y, W, y + RH], fill=rgb(C_PANEL if i % 2 == 0 else C_BG))

        # 인디케이터 점
        d.ellipse([40, y + 26, 50, y + 36],
                  fill=rgb(C_THEME if is_theme else C_DOWN))

        # 섹터/테마 이름
        name_col = C_THEME if is_theme else C_TEXT
        nf       = font(True, 20) if is_theme else font(False, 20)
        d.text((60, y + 20), s["name"], font=nf, fill=rgb(name_col))

        # % 배지 (고정 위치 오른쪽)
        is_up = s["pct"] >= 0
        pbg   = UP_DIM if is_up else DOWN_DIM
        pfg   = C_UP   if is_up else C_DOWN
        d.rounded_rectangle([742, y + 16, 860, y + 48], radius=16, fill=rgb(pbg))
        d.text((754, y + 24), pct_arrow(s["pct"]), font=font(True, 14), fill=rgb(pfg))

        # 행 구분선
        d.line([(40, y + RH - 1), (W - 40, y + RH - 1)], fill=rgb(C_BORDER), width=1)

    d.text((40, HH + n * RH + 14), "★ 테마: 네이버 금융  |  업종: pykrx KOSPI 분류",
           font=font(False, 11), fill=rgb("#4A5568"))

    p = OUT / "03_sector_card.png"
    img.save(p)
    print(f"✓ 섹터 카드   : {p.name}  ({n}개 항목)")


# ══════════════════════════════════════════════
# 4. 체크포인트 카드 (900 × auto)
# ══════════════════════════════════════════════
def make_checkpoint():
    items  = D["checkpoint"]
    n      = len(items)
    ROW_H  = 76
    HDRH   = 80
    FTRH   = 36
    H      = max(420, HDRH + n * ROW_H + FTRH)
    W      = 900

    img = Image.new("RGB", (W, H), rgb(C_BG))
    d   = ImageDraw.Draw(img)

    # 상단 강조 바 (골드)
    d.rectangle([0, 0, W, 4], fill=rgb(C_GOLD))

    d.text((40, 20), "내일 체크포인트", font=font(True, 22), fill=rgb(C_TEXT))
    d.text((40, 50), "우산 챙길까, 접을까?", font=font(False, 14), fill=rgb(C_DIM))

    df  = font(False, 13)
    dtw = _tw(d, D["date_short"], df)
    d.text((W - 40 - dtw, 26), D["date_short"], font=df, fill=rgb(C_DIM))

    d.line([(40, 72), (W - 40, 72)], fill=rgb(C_BORDER), width=1)

    for i, item in enumerate(items):
        y = HDRH + i * ROW_H
        d.rectangle([0, y, W, y + ROW_H], fill=rgb(C_PANEL if i % 2 == 0 else C_BG))

        # 번호 원형 배지
        bcol    = CHECKPOINT_BADGE_COLORS[i % len(CHECKPOINT_BADGE_COLORS)]
        cx, cy  = 58, y + 38
        d.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=rgb(bcol))
        nf  = font(True, 15)
        num = str(i + 1)
        ntw = _tw(d, num, nf)
        d.text((cx - ntw // 2, cy - 10), num, font=nf, fill=rgb(C_BG))

        # 항목 텍스트
        d.text((92, y + 24), item, font=font(True, 20), fill=rgb(C_TEXT))

        # 행 구분선
        d.line([(40, y + ROW_H - 1), (W - 40, y + ROW_H - 1)],
               fill=rgb(C_BORDER), width=1)

    d.text((40, HDRH + n * ROW_H + 12), "정보 제공 목적이며 투자 권유가 아닙니다",
           font=font(False, 11), fill=rgb("#4A5568"))

    p = OUT / "04_checkpoint_card.png"
    img.save(p)
    print(f"✓ 체크포인트  : {p.name}")


# ── 실행 ───────────────────────────────────────
if __name__ == "__main__":
    print(f"\n[장마여 카드 생성] {D['date_str']} → {OUT}\n")
    make_thumbnail()
    make_supply()
    make_sector()
    make_checkpoint()
    print(f"\n완료. 이미지 위치: {OUT}")
