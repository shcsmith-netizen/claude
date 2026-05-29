# coding: utf-8
"""
장마감후여기 — 네이버 블로그 스킨 13장 자동 업로드

사용 방법:
  1) 디버그크롬_실행.bat 실행 후 네이버 로그인
  2) py upload_skin.py 실행

UI 흐름:
  왼쪽 메뉴 클릭 → [직접등록] 탭 클릭 → [파일 등록] 버튼 클릭 → 파일 선택
  - 스킨배경: 상단(btn 0), 하단(btn 1)
  - 전체 박스, 구성 박스, 프로필: 제목(0)/내용(1)/하단(2) 또는 상단(0)/중앙(1)/하단(2)
"""

import asyncio
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.async_api import async_playwright

CDP_URL = "http://localhost:9222"
ASSETS_DIR = Path(r"C:\Users\l\scripts\econ_auto\assets\blog_design\skin")
LOGS_DIR = Path(r"C:\Users\l\scripts\econ_auto\logs\skin_upload")
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# (왼쪽 메뉴 라벨, PNG 파일, "파일 등록" 버튼 순서(0=첫번째))
# 같은 메뉴에 여러 파일: 버튼 인덱스로 구분
UPLOAD_MAP = [
    ("스킨배경",   ASSETS_DIR / "01_bg_top_3000x500.png",     0),  # 상단 영역
    ("스킨배경",   ASSETS_DIR / "02_bg_bottom_3000x400.png",   1),  # 하단 영역(패턴)
    ("블로그 메뉴", ASSETS_DIR / "04_menu_966x60.png",         0),
    ("전체 박스",  ASSETS_DIR / "05_wrap_header_982x60.png",   0),  # 제목 영역
    ("전체 박스",  ASSETS_DIR / "06_wrap_content_982x100.png", 1),  # 내용 영역
    ("전체 박스",  ASSETS_DIR / "07_wrap_footer_982x60.png",   2),  # 하단 영역
    ("구성 박스",  ASSETS_DIR / "08_widget_header_171x40.png", 0),  # 제목 영역
    ("구성 박스",  ASSETS_DIR / "09_widget_content_171x40.png",1),  # 내용 영역
    ("구성 박스",  ASSETS_DIR / "10_widget_footer_171x30.png", 2),  # 하단 영역
    ("프로필",     ASSETS_DIR / "11_profile_top_171x40.png",   0),  # 상단 영역
    ("프로필",     ASSETS_DIR / "12_profile_mid_171x40.png",   1),  # 중앙 영역
    ("프로필",     ASSETS_DIR / "13_profile_bottom_171x30.png",2),  # 하단 영역
]


def save_html(content, name):
    path = LOGS_DIR / f"{name}.html"
    path.write_text(content[:100000], encoding="utf-8")
    print(f"  [HTML저장] {path.name}")


async def find_blog_id(page):
    """로그인된 사용자의 blogId 추출"""
    await page.goto("https://blog.naver.com/")
    await page.wait_for_timeout(2000)
    try:
        href = await page.evaluate("""
            () => {
                const a = document.querySelector('a[href*="blog.naver.com/"][href*="MyBlog"]')
                       || document.querySelector('a[href*="PostList.naver"]')
                       || document.querySelector('a[href*="blog.naver.com/"]');
                return a ? a.href : null;
            }
        """)
        if href and "blog.naver.com/" in href:
            blog_id = href.split("blog.naver.com/")[1].split("/")[0].split("?")[0]
            if blog_id and blog_id not in ("PostList.naver", "MyBlog.naver", ""):
                return blog_id
    except Exception:
        pass
    return None


async def open_remocon(page, blog_id):
    """Remocon.naver URL 직접 이동"""
    remocon_url = (
        f"https://admin.blog.naver.com/Remocon.naver"
        f"?blogId={blog_id}&loadType=admin&Redirect=Remocon"
    )
    print(f"  리모콘 직접 이동: {remocon_url}")
    await page.goto(remocon_url)
    await page.wait_for_timeout(4000)
    await page.screenshot(path=str(LOGS_DIR / "step2_remocon.png"))
    print(f"  현재 URL: {page.url}")
    print(f"  프레임 수: {len(page.frames)}")
    for i, f in enumerate(page.frames):
        print(f"    [{i}] {f.url}")


async def get_all_frames(page):
    """메인 프레임 + 모든 iframe"""
    frames = [page.main_frame]
    for frame in page.frames:
        if frame != page.main_frame:
            frames.append(frame)
    return frames


async def find_element_in_frames(page, selector):
    """모든 프레임에서 selector로 요소 탐색, 첫 번째 매칭 반환"""
    for frame in await get_all_frames(page):
        try:
            el = await frame.query_selector(selector)
            if el:
                return frame, el
        except Exception:
            continue
    return None, None


async def find_all_elements_in_frames(page, selector):
    """모든 프레임에서 selector로 요소 전체 탐색"""
    results = []
    for frame in await get_all_frames(page):
        try:
            els = await frame.query_selector_all(selector)
            for e in els:
                results.append((frame, e))
        except Exception:
            continue
    return results


async def click_menu_item(page, menu_label):
    """왼쪽 메뉴에서 항목 클릭"""
    frame, el = await find_element_in_frames(page, f'text="{menu_label}"')
    if not frame:
        return False
    await el.click(timeout=5000)
    await page.wait_for_timeout(1000)
    return True


async def click_direct_tab(page):
    """직접등록 탭 클릭 (탭 전환)"""
    _, tab = await find_element_in_frames(page, 'text="직접등록"')
    if tab:
        await tab.click()
        await page.wait_for_timeout(800)
        return True
    return False


async def upload_one(page, menu_label, file_path, btn_idx, idx, total):
    """단일 영역 PNG 업로드

    btn_idx: 직접등록 탭 내 "파일 등록" 버튼 순서 (0=첫번째)
    """
    print(f"\n[{idx}/{total}] {menu_label}[버튼{btn_idx}] ← {file_path.name}")
    if not file_path.exists():
        print(f"  [건너뜀] 파일 없음: {file_path}")
        return False

    try:
        # 1) 왼쪽 메뉴 클릭
        ok = await click_menu_item(page, menu_label)
        if not ok:
            print(f"  [경고] '{menu_label}' 메뉴 못 찾음")
            await page.screenshot(path=str(LOGS_DIR / f"{idx:02d}_not_found.png"))
            return False
        print(f"  '{menu_label}' 클릭 완료")

        # 2) 직접등록 탭 클릭
        tab_ok = await click_direct_tab(page)
        if tab_ok:
            print(f"  '직접등록' 탭 전환 완료")
        else:
            print(f"  [주의] '직접등록' 탭 못 찾음, 계속 진행")

        await page.screenshot(path=str(LOGS_DIR / f"{idx:02d}_after_tab.png"))

        # 3) "파일 등록" 버튼 목록 수집
        all_file_btns = await find_all_elements_in_frames(page, 'text="파일 등록"')
        print(f"  '파일 등록' 버튼 수: {len(all_file_btns)}")

        if not all_file_btns:
            print(f"  [경고] '파일 등록' 버튼 못 찾음 — DOM 덤프")
            await page.screenshot(path=str(LOGS_DIR / f"{idx:02d}_no_btn.png"))
            dom = await page.evaluate(
                "() => document.body.innerHTML.substring(0, 10000)"
            )
            (LOGS_DIR / f"{idx:02d}_dom.txt").write_text(dom, encoding="utf-8")
            return False

        actual_idx = min(btn_idx, len(all_file_btns) - 1)
        if actual_idx != btn_idx:
            print(f"  [주의] 버튼 {btn_idx}번 없음, {actual_idx}번 사용")

        _, file_btn = all_file_btns[actual_idx]

        # 4) 파일 선택 (파일등록 버튼 → 파일 선택 다이얼로그)
        async with page.expect_file_chooser(timeout=8000) as fc_info:
            await file_btn.click()
        file_chooser = await fc_info.value
        await file_chooser.set_files(str(file_path))

        await page.wait_for_timeout(2000)
        print(f"  [업로드 완료]")
        await page.screenshot(path=str(LOGS_DIR / f"{idx:02d}_done.png"))
        return True

    except Exception as e:
        print(f"  [에러] {e}")
        await page.screenshot(path=str(LOGS_DIR / f"{idx:02d}_error.png"))
        return False


async def main():
    print("=" * 60)
    print("장마감후여기 — 스킨 13장 자동 업로드")
    print("=" * 60)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"\n[오류] Chrome 디버그 연결 실패: {e}")
            print("→ '디버그크롬_실행.bat' 먼저 실행 후 네이버 로그인하세요.")
            sys.exit(1)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        # 1) blogId
        print("\n[1/4] blogId 확인...")
        blog_id = await find_blog_id(page)
        if not blog_id:
            print("  자동 추출 실패. 직접 입력:")
            blog_id = input("  blogId: ").strip()
        print(f"  blogId = {blog_id}")

        # 2) 리모콘 열기
        print("\n[2/4] 리모콘 열기...")
        await open_remocon(page, blog_id)

        # 3) 리모콘 확인 후 시작
        print()
        print("  ─────────────────────────────────────────────────────")
        print("  리모콘이 화면에 보이면 Enter. (안 보이면 수동으로 열고 Enter)")
        print("  ─────────────────────────────────────────────────────")
        input("  Enter: ")
        await page.screenshot(path=str(LOGS_DIR / "step3_ready.png"))

        # 4) 13장 업로드
        print("\n[3/4] 13장 업로드...")
        success = 0
        for idx, (menu_label, fpath, btn_idx) in enumerate(UPLOAD_MAP, 1):
            ok = await upload_one(page, menu_label, fpath, btn_idx, idx, len(UPLOAD_MAP))
            if ok:
                success += 1
            await page.wait_for_timeout(500)

        # 5) 적용
        print("\n[4/4] 적용 버튼...")
        for txt in ["적용", "스킨 적용", "저장"]:
            _, el = await find_element_in_frames(page, f'text="{txt}"')
            if el:
                await el.click()
                print(f"  '{txt}' 클릭됨")
                await page.wait_for_timeout(2000)
                break
        await page.screenshot(path=str(LOGS_DIR / "99_final.png"))

        print()
        print("=" * 60)
        print(f"완료: {success}/{len(UPLOAD_MAP)} 성공")
        print(f"캡처·로그: {LOGS_DIR}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
