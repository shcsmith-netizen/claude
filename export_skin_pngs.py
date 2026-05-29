# coding: utf-8
"""
장마감후여기 — 네이버 블로그 스킨 13개 영역 PNG 추출

각 영역을 [data-skin="..."] 속성으로 구분해서 개별 캡처.
"""

import asyncio
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

SRC_HTML = Path(r"C:\Users\l\scripts\econ_auto\skin_design.html")
OUTPUT_DIR = Path(r"C:\Users\l\scripts\econ_auto\assets\blog_design\skin")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# (data-skin 값, 출력 파일명, 가로, 세로)
ASSETS = [
    ("bg_top",         "01_bg_top_3000x500",         3000, 500),
    ("bg_bottom",      "02_bg_bottom_3000x400",      3000, 400),
    ("title",          "03_title_966x400",           966,  400),
    ("menu",           "04_menu_966x60",             966,  60),
    ("wrap_header",    "05_wrap_header_982x60",      982,  60),
    ("wrap_content",   "06_wrap_content_982x100",    982,  100),
    ("wrap_footer",    "07_wrap_footer_982x60",      982,  60),
    ("widget_header",  "08_widget_header_171x40",    171,  40),
    ("widget_content", "09_widget_content_171x40",   171,  40),
    ("widget_footer",  "10_widget_footer_171x30",    171,  30),
    ("profile_top",    "11_profile_top_171x40",      171,  40),
    ("profile_mid",    "12_profile_mid_171x40",      171,  40),
    ("profile_bottom", "13_profile_bottom_171x30",   171,  30),
]


async def main():
    url = SRC_HTML.as_uri()
    print(f"[로드] {url}")
    print(f"[출력] {OUTPUT_DIR}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # 가장 큰 영역(3000×500)을 한 번에 담을 수 있는 viewport
        context = await browser.new_context(
            viewport={"width": 3200, "height": 1200},
            device_scale_factor=2,  # 고해상도 2배
        )
        page = await context.new_page()
        await page.goto(url)

        # 폰트 로드 대기
        await page.wait_for_timeout(1500)

        for skin_id, filename, w, h in ASSETS:
            selector = f'[data-skin="{skin_id}"]'
            element = await page.query_selector(selector)
            if not element:
                print(f"  [건너뜀] {skin_id}")
                continue
            out = OUTPUT_DIR / f"{filename}.png"
            await element.screenshot(path=str(out))
            print(f"  [저장] {filename}.png  ({w}×{h})")

        await browser.close()

    print()
    print("=" * 60)
    print(f"완료. 총 {len(ASSETS)}개 PNG 생성")
    print(f"폴더: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
