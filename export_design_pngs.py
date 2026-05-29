# coding: utf-8
"""
장마감후여기 블로그 디자인 HTML → 각 요소 PNG 추출 스크립트

원본: C:\\Users\\l\\Downloads\\장마감후여기 블로그 디자인.html
- 외부 jsx 파일(design-canvas.jsx, tweaks-panel.jsx) 누락 상태이므로
- 인라인 wrapper로 치환한 export.html을 임시 생성 후
- Playwright로 각 DCArtboard 요소를 개별 PNG로 캡처

출력: C:\\Users\\l\\scripts\\econ_auto\\assets\\blog_design\\
"""

import asyncio
import sys
from pathlib import Path

# Windows cp949 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

SRC_HTML = Path(r"C:\Users\l\Downloads\장마감후여기 블로그 디자인.html")
EXPORT_HTML = Path(r"C:\Users\l\Downloads\장마감후여기_export_temp.html")
OUTPUT_DIR = Path(r"C:\Users\l\scripts\econ_auto\assets\blog_design")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 캡처 대상: (artboard id, 출력 파일명, 폭, 높이)
ARTBOARDS = [
    ("colors",   "01_palette_820x120",          820, 120),
    ("bannerB",  "02_banner_966x350",           966, 350),
    ("profileA", "03_profile_umbrella_200",     200, 200),
    ("profileB", "04_profile_dark_200",         200, 200),
    ("mobileB",  "05_mobile_cover_360",         360, 360),
    ("skinA",    "06_skin_mockup_800x660",      800, 660),
    ("catIcons", "07_category_icons_620x140",   620, 140),
    ("postCard", "08_first_post_card_640x560",  640, 560),
]

# DesignCanvas / DCSection / DCArtboard wrapper (인라인 jsx)
DC_WRAPPER = """
function DesignCanvas({ children }) {
  return (<div style={{padding: 24, background: '#1a1a1a', minHeight: '100vh'}}>{children}</div>);
}
function DCSection({ id, title, children }) {
  return (
    <section data-section={id} style={{margin: '24px 0'}}>
      <h2 style={{color: '#fff', marginBottom: 12, fontSize: 18, fontFamily: 'Pretendard'}}>{title}</h2>
      <div style={{display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start'}}>{children}</div>
    </section>
  );
}
function DCArtboard({ id, label, width, height, children }) {
  return (
    <div style={{display: 'inline-block'}}>
      <div style={{color: '#aaa', fontSize: 12, marginBottom: 6, fontFamily: 'Pretendard'}}>{label}</div>
      <div data-artboard={id} style={{width: width + 'px', height: height + 'px', overflow: 'hidden', background: '#fff'}}>
        {children}
      </div>
    </div>
  );
}
"""

# TweaksPanel/useTweaks 더미 (모든 섹션 표시)
TWEAKS_WRAPPER = """
window.TweaksPanel = function TweaksPanel({ children }) { return null; };
window.TweakSection = function TweakSection() { return null; };
window.TweakToggle = function TweakToggle() { return null; };
window.useTweaks = function useTweaks(init) { return [init, function() {}]; };
"""


def prepare_export_html():
    """원본 HTML의 외부 jsx 라인을 인라인 wrapper로 치환한 임시 파일 생성"""
    print(f"[1/3] 원본 HTML 읽는 중: {SRC_HTML}")
    src = SRC_HTML.read_text(encoding="utf-8")

    src = src.replace(
        '<script type="text/babel" src="design-canvas.jsx"></script>',
        f'<script type="text/babel">{DC_WRAPPER}</script>',
    )
    src = src.replace(
        '<script type="text/babel" src="tweaks-panel.jsx"></script>',
        f'<script type="text/babel">{TWEAKS_WRAPPER}</script>',
    )

    EXPORT_HTML.write_text(src, encoding="utf-8")
    print(f"[2/3] 임시 export HTML 생성: {EXPORT_HTML}")


async def capture_pngs():
    print(f"[3/3] Playwright로 캡처 시작 → {OUTPUT_DIR}")
    url = EXPORT_HTML.as_uri()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,  # 고해상도
        )
        page = await context.new_page()

        # 콘솔 에러 출력
        page.on("pageerror", lambda e: print(f"  [페이지 에러] {e}"))
        page.on("console", lambda msg: (
            print(f"  [콘솔 {msg.type}] {msg.text}") if msg.type in ("error", "warning") else None
        ))

        await page.goto(url)

        # React 렌더 대기
        try:
            await page.wait_for_selector("[data-artboard]", timeout=20000)
        except Exception as e:
            print(f"  [경고] artboard 로드 타임아웃: {e}")
            html = await page.content()
            print("  [페이지 일부]", html[:500])
            await browser.close()
            return

        # 폰트/SVG 안정화
        await page.wait_for_timeout(2000)

        for artboard_id, filename, w, h in ARTBOARDS:
            selector = f'[data-artboard="{artboard_id}"]'
            element = await page.query_selector(selector)
            if not element:
                print(f"  [건너뜀] {artboard_id} 요소 못 찾음")
                continue

            out = OUTPUT_DIR / f"{filename}.png"
            await element.screenshot(path=str(out))
            print(f"  [저장] {out.name} ({w}x{h})")

        await browser.close()


def main():
    if not SRC_HTML.exists():
        print(f"[오류] 원본 파일 없음: {SRC_HTML}")
        sys.exit(1)

    prepare_export_html()
    asyncio.run(capture_pngs())

    print()
    print("=" * 60)
    print(f"완료. 출력 폴더: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
