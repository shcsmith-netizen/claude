"""Naver 에디터 사진 버튼 클릭 후 DOM 변화 진단"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BLOG_ID    = "labandiera"
PROFILE_DIR = Path(__file__).parent / ".playwright_profile"
LOG_DIR     = Path(__file__).parent / "logs"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        write_url = f"https://blog.naver.com/PostWriteForm.naver?blogId={BLOG_ID}"
        await page.goto(write_url, wait_until="load", timeout=60000)
        await asyncio.sleep(8)
        print("현재 URL:", page.url)

        # 1. 사진 버튼 찾기
        print("\n[1] 사진 버튼 찾기:")
        try:
            btn = page.get_by_role("button", name="사진", exact=False)
            count = await btn.count()
            print(f"  get_by_role('button', '사진') count: {count}")
            if count > 0:
                vis = await btn.first.is_visible(timeout=1000)
                print(f"  첫 번째 버튼 visible: {vis}")
        except Exception as e:
            print(f"  에러: {e}")

        btn2 = page.locator("button.se-image-toolbar-button")
        count2 = await btn2.count()
        print(f"  button.se-image-toolbar-button count: {count2}")
        if count2 > 0:
            vis2 = await btn2.first.is_visible(timeout=1000)
            print(f"  첫 번째 visible: {vis2}")
            bb = await btn2.first.bounding_box()
            print(f"  bounding_box: {bb}")

        # 2. 클릭 전 file input 수
        fi_before = await page.locator("input[type='file']").count()
        print(f"\n[2] 클릭 전 input[type='file'] count: {fi_before}")

        # 3. 사진 버튼 클릭 시도
        print("\n[3] 사진 버튼 클릭:")
        try:
            await btn2.first.click(timeout=5000)
            print("  클릭 성공")
        except Exception as e:
            print(f"  클릭 에러: {e}")

        await asyncio.sleep(2)

        # 4. 클릭 후 DOM 변화
        fi_after = await page.locator("input[type='file']").count()
        print(f"\n[4] 클릭 후 input[type='file'] count: {fi_after}")

        # 모든 프레임에서 file input 탐색
        for i, frame in enumerate(page.frames):
            try:
                fi = await frame.locator("input[type='file']").count()
                if fi > 0:
                    print(f"  Frame {i}: {fi}개 input[type='file'] 발견!")
            except:
                pass

        # 5. 클릭 후 팝업/패널 탐색
        popup_result = await page.evaluate("""() => {
            const panels = document.querySelectorAll('.se-popup, .se-dialog, .se-modal, [class*="upload"], [class*="photo"], [class*="image-upload"]');
            return {
                count: panels.length,
                items: Array.from(panels).slice(0,10).map(el => ({
                    class: (el.className||'').slice(0,60),
                    visible: window.getComputedStyle(el).display !== 'none',
                    text: (el.innerText||'').slice(0,50)
                }))
            };
        }""")
        print(f"\n[5] 클릭 후 팝업/패널: {popup_result}")

        # 6. 새로 나타난 버튼들
        new_buttons = await page.evaluate("""() => {
            const isVisible = (el) => {
                if (!(el instanceof HTMLElement) || !el.isConnected) return false;
                const s = window.getComputedStyle(el);
                if (s.display==='none'||s.visibility==='hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width>0 && r.height>0;
            };
            return Array.from(document.querySelectorAll('button,[role="button"]'))
                .filter(isVisible)
                .filter(el => {
                    const t = (el.innerText||'').toLowerCase() + (el.getAttribute('aria-label')||'').toLowerCase();
                    return t.includes('pc') || t.includes('파일') || t.includes('올리기') || t.includes('불러오기');
                })
                .map(el => ({
                    class: (el.className||'').slice(0,60),
                    text: (el.innerText||'').slice(0,30),
                    aria: el.getAttribute('aria-label')||''
                }));
        }""")
        print(f"\n[6] PC/파일 관련 버튼: {new_buttons}")

        # 스크린샷
        await page.screenshot(path=str(LOG_DIR / "debug_click.png"), full_page=False)
        print("\n스크린샷 저장: debug_click.png")

        # 7. 내 PC 버튼 클릭 시도
        if new_buttons:
            print("\n[7] 내 PC 버튼 클릭 시도...")
            for label in ["내 PC에서 불러오기", "내 PC", "PC에서 불러오기", "파일 선택"]:
                try:
                    b = page.get_by_text(label, exact=True).first
                    if await b.is_visible(timeout=500):
                        await b.click()
                        print(f"  '{label}' 클릭 성공")
                        await asyncio.sleep(1.5)
                        fi_after2 = await page.locator("input[type='file']").count()
                        print(f"  클릭 후 file input: {fi_after2}")
                        await page.screenshot(path=str(LOG_DIR / "debug_after_pc.png"), full_page=False)
                        break
                except:
                    continue

        await asyncio.sleep(5)
        await browser.close()

asyncio.run(main())
