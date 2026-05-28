#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장마감후여기 카드 이미지 생성기
포스트 txt 파일 → 섹션별 PNG 카드 이미지 생성
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re
import sys

CARD_W, CARD_H = 1080, 1080
PAD = 72

BG        = (13, 17, 23)
ACCENT    = (79, 195, 247)
WHITE     = (240, 246, 252)
GRAY      = (139, 148, 158)
DARK_GRAY = (48, 54, 61)
FOOTER_BG = (20, 25, 33)

FONT_PATHS_BOLD = [
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/NanumGothicBold.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
FONT_PATHS_REG = [
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = FONT_PATHS_BOLD if bold else FONT_PATHS_REG
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """픽셀 기준 한국어 텍스트 줄바꿈"""
    lines = []
    current = ""
    for ch in text:
        candidate = current + ch
        w = draw.textbbox((0, 0), candidate, font=font)[2]
        if w > max_w and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def parse_table(text: str) -> list[list[str]]:
    """마크다운 표 → [[cell, ...], ...] (구분선 행 제외)"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r'^\|[-| :]+\|$', s):
            continue
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            cells = [re.sub(r'\*\*(.+?)\*\*', r'\1', c) for c in cells]
            rows.append(cells)
    return rows


def _strip_md(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    return text


def draw_card(
    title: str,
    subtitle: str = "",
    bullet_lines: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
    date_str: str = "",
    tag: str = "",
) -> Image.Image:
    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    d = ImageDraw.Draw(img)

    f_logo  = _load_font(26, bold=True)
    f_tag   = _load_font(26, bold=True)
    f_title = _load_font(54, bold=True)
    f_sub   = _load_font(29)
    f_body  = _load_font(30)
    f_tbl   = _load_font(25)
    f_small = _load_font(23)

    # 상단 액센트 바
    d.rectangle([0, 0, CARD_W, 7], fill=ACCENT)

    # 날짜 (좌상단)
    y_top = 32
    if date_str:
        d.text((PAD, y_top), date_str, fill=GRAY, font=f_small)

    # 로고 (우상단)
    logo = "장마감후여기"
    lw = d.textbbox((0, 0), logo, font=f_logo)[2]
    d.text((CARD_W - PAD - lw, y_top), logo, fill=ACCENT, font=f_logo)

    y = y_top + 48

    # 태그 배지
    if tag:
        tw = d.textbbox((0, 0), tag, font=f_tag)[2] + 28
        d.rounded_rectangle([PAD, y, PAD + tw, y + 44], radius=8, fill=ACCENT)
        d.text((PAD + 14, y + 8), tag, fill=BG, font=f_tag)
        y += 60

    # 제목 (최대 2줄)
    y += 10
    title_lines = _wrap(d, title, f_title, CARD_W - PAD * 2)
    for line in title_lines[:2]:
        d.text((PAD, y), line, fill=WHITE, font=f_title)
        y += 68

    # 서브타이틀
    if subtitle:
        d.text((PAD, y), subtitle, fill=GRAY, font=f_sub)
        y += 44

    # 구분선
    y += 12
    d.rectangle([PAD, y, CARD_W - PAD, y + 2], fill=ACCENT)
    y += 22

    content_bottom = CARD_H - 80

    # 불릿 목록
    if bullet_lines:
        for line in bullet_lines:
            if y >= content_bottom - 10:
                break
            line = _strip_md(line.strip()).lstrip("-•▶ ").strip()
            if not line:
                continue
            # 불릿 점
            d.ellipse([PAD, y + 13, PAD + 10, y + 23], fill=ACCENT)
            # 텍스트 줄바꿈
            parts = _wrap(d, line, f_body, CARD_W - PAD * 2 - 24)
            for j, part in enumerate(parts[:2]):
                d.text((PAD + 22, y), part, fill=WHITE, font=f_body)
                y += 40
            y += 8

    # 표
    if table_rows:
        n_cols = max(len(r) for r in table_rows)
        if n_cols > 0:
            col_w = (CARD_W - PAD * 2) // n_cols
            # 헤더
            if table_rows:
                for j, cell in enumerate(table_rows[0][:n_cols]):
                    d.text((PAD + j * col_w, y), cell, fill=ACCENT, font=f_tbl)
                y += 36
                d.rectangle([PAD, y, CARD_W - PAD, y + 1], fill=DARK_GRAY)
                y += 14
            # 데이터
            for row in table_rows[1:]:
                if y >= content_bottom - 10:
                    break
                for j, cell in enumerate(row[:n_cols]):
                    x = PAD + j * col_w
                    max_cell_w = col_w - 8
                    while cell and d.textbbox((0, 0), cell, font=f_tbl)[2] > max_cell_w:
                        cell = cell[:-1]
                    if not row[j].endswith(cell) or len(row[j]) > len(cell):
                        cell = cell.rstrip() + "…" if cell else ""
                    d.text((x, y), cell, fill=WHITE, font=f_tbl)
                y += 38

    # 하단 풋터
    d.rectangle([0, CARD_H - 54, CARD_W, CARD_H], fill=FOOTER_BG)
    d.rectangle([0, CARD_H - 55, CARD_W, CARD_H - 54], fill=DARK_GRAY)
    d.text((PAD, CARD_H - 38), "장마감후여기 · 장마여", fill=GRAY, font=f_small)
    disc = "본 카드는 정보 제공 목적입니다"
    dw = d.textbbox((0, 0), disc, font=f_small)[2]
    d.text((CARD_W - PAD - dw, CARD_H - 38), disc, fill=DARK_GRAY, font=f_small)

    return img


def _get_date_str(stem: str) -> str:
    """260503_01_... → '2026.05.03'"""
    code = stem.split("_")[0]
    if len(code) == 6 and code.isdigit():
        return f"20{code[:2]}.{code[2:4]}.{code[4:6]}"
    return ""


def generate_cards(post_path: Path, output_dir: Path) -> list[Path]:
    """
    포스트 txt → 카드 PNG 생성.
    [이미지] 마커 수만큼 카드를 생성해 output_dir에 저장.
    생성된 Path 리스트 반환.
    """
    try:
        text = post_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[card_generator] 파일 읽기 실패: {e}")
        return []

    if "---" not in text:
        return []
    header, _, body = text.partition("---")
    body = body.strip()

    img_count = body.count("[이미지]")
    if img_count == 0:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = _get_date_str(post_path.stem)
    sections = body.split("[이미지]")

    paths = []
    for i in range(img_count):
        before = sections[i]
        after  = sections[i + 1] if i + 1 < len(sections) else ""

        # 이 이미지가 속한 섹션 제목: before 마지막 ## heading
        headings = re.findall(r'^##\s+(.+)$', before, re.MULTILINE)
        heading = headings[-1].strip() if headings else f"섹션 {i + 1}"

        if "—" in heading:
            title, _, sub = heading.partition("—")
            title, sub = title.strip(), sub.strip()
        elif " - " in heading:
            title, _, sub = heading.partition(" - ")
            title, sub = title.strip(), sub.strip()
        else:
            title, sub = heading, ""

        # after 부분에서 다음 ━━━ 전까지 콘텐츠 추출
        content = after.split("━━━")[0].strip()
        content = re.sub(r'^##\s+.+$', '', content, flags=re.MULTILINE).strip()

        # 표 우선, 없으면 불릿/텍스트
        table_rows = parse_table(content)
        bullets    = None

        if not table_rows:
            raw_lines = []
            for line in content.splitlines():
                line = _strip_md(line.strip())
                if not line or line.startswith("|") or re.match(r'^━+$', line):
                    continue
                if len(line) >= 10:
                    raw_lines.append(line)
                    if len(raw_lines) >= 6:
                        break
            bullets = raw_lines if raw_lines else None

        # 첫 번째 카드는 도입부 핵심 3줄 우선 사용
        if i == 0:
            intro = sections[0]
            key = []
            for line in intro.splitlines():
                s = _strip_md(line.strip())
                if s.startswith("- ") or s.startswith("• "):
                    key.append(s[2:].strip())
            if key:
                bullets    = key[:5]
                table_rows = None

        # 태그
        tag_map = {
            "주간": "주간시황", "글로벌": "글로벌매크로",
            "캘린더": "다음주일정", "코멘트": "장마여한마디",
            "수급": "수급흐름", "섹터": "오늘섹터",
            "체크": "내일체크", "마감": "마감브리핑",
        }
        tag = ""
        for k, v in tag_map.items():
            if k in title or k in sub:
                tag = v
                break
        if not tag:
            tag = f"CARD {i + 1}"

        img = draw_card(
            title=title,
            subtitle=sub,
            bullet_lines=bullets,
            table_rows=table_rows,
            date_str=date_str,
            tag=tag,
        )

        out = output_dir / f"{i + 1:02d}.png"
        img.save(out, "PNG")
        print(f"[card_generator] 카드 생성: {out.name}")
        paths.append(out)

    return paths


if __name__ == "__main__":
    # 직접 실행: py card_generator.py [txt_파일_경로]
    BASE = Path(__file__).parent
    PENDING = BASE / "posts" / "pending"
    IMAGES  = BASE / "posts" / "images"

    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = sorted(PENDING.glob("*.txt"))

    if not targets:
        print("생성할 포스트가 없습니다. posts/pending/ 폴더를 확인하세요.")
        sys.exit(0)

    for txt in targets:
        stem  = txt.stem
        parts = stem.split("_", 2)
        code  = (parts[0] + parts[1]) if len(parts) >= 2 else stem[:8]
        out_dir = IMAGES / code
        print(f"\n처리 중: {txt.name}")
        generated = generate_cards(txt, out_dir)
        print(f"  → {len(generated)}장 생성 ({out_dir})")
