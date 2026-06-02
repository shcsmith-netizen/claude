from __future__ import annotations
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장마감후여기 블로그 즉시 발행 스크립트
posts/pending/*.txt → 네이버 블로그 즉시 발행 → posts/done/ 이동

이미지: posts/images/YYMMDD/ 폴더의 PNG 파일을 [이미지] 마커 순서대로 삽입
파일명: YYMMDDXX_카테고리_제목.txt
"""

import asyncio
import base64
import html as html_mod
import io
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright, Page
except ImportError:
    print("Playwright 설치 필요: py -m pip install playwright && py -m playwright install chromium")
    sys.exit(1)

from econ_config import NAVER_ID, NAVER_PW, BLOG_ID


def parse_schedule(filename: str) -> datetime | None:
    """파일명 YYMMDDTT_... 에서 예약 시각 파싱. TT=시(00~23). 현재 이후면 반환, 아니면 None."""
    stem = Path(filename).stem
    code = stem.split("_")[0]
    if not code.isdigit() or len(code) not in (8, 10):
        return None
    year = 2000 + int(code[0:2])
    month = int(code[2:4])
    day = int(code[4:6])
    hour = int(code[6:8])
    minute = int(code[8:10]) if len(code) == 10 else 0
    dt = datetime(year, month, day, hour, minute)
    return dt if dt > datetime.now() else None

# 경로 설정
BASE_DIR    = Path(__file__).parent
PENDING_DIR = BASE_DIR / "posts" / "pending"
DONE_DIR    = BASE_DIR / "posts" / "done"
IMAGES_DIR  = BASE_DIR / "posts" / "images"
LOG_DIR     = BASE_DIR / "logs"
PROFILE_DIR = BASE_DIR / ".playwright_profile"

for d in (DONE_DIR, LOG_DIR, PROFILE_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "publisher.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── 유틸 ──

def set_clipboard(text: str):
    """Windows 클립보드에 유니코드 텍스트 복사 (ctypes 방식, 한글 안정)"""
    import ctypes
    CF_UNICODETEXT = 13
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    text_bytes = (text + "\x00").encode("utf-16-le")
    hMem = k32.GlobalAlloc(0x0002, len(text_bytes))  # GMEM_MOVEABLE
    pMem = k32.GlobalLock(hMem)
    ctypes.memmove(pMem, text_bytes, len(text_bytes))
    k32.GlobalUnlock(hMem)
    u32.OpenClipboard(None)
    u32.EmptyClipboard()
    u32.SetClipboardData(CF_UNICODETEXT, hMem)
    u32.CloseClipboard()


def parse_post(path: Path):
    """txt 파일 → (제목, 카테고리, 이미지키워드, 본문)"""
    text = path.read_text(encoding="utf-8")
    if "---" not in text:
        log.error(f"형식 오류 (--- 없음): {path.name}")
        return None
    header, _, body = text.partition("---")
    meta = {"제목": "", "카테고리": "", "이미지키워드": ""}
    for line in header.splitlines():
        m = re.match(r"^(제목|카테고리|이미지키워드)\s*:\s*(.+)$", line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta["제목"], meta["카테고리"], meta["이미지키워드"], body.strip()


def get_image_paths(filename: str) -> list[Path]:
    stem = Path(filename).stem
    exact_dir = IMAGES_DIR / stem
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    if exact_dir.is_dir():
        return sorted(p for p in exact_dir.iterdir() if p.suffix.lower() in exts and not p.name.startswith("."))
    
    parts = stem.split("_")
    date_code = (parts[0] + parts[1]) if len(parts) >= 2 and parts[1].isdigit() else stem[:8]
    img_dir = IMAGES_DIR / date_code
    if not img_dir.is_dir():
        img_dir = IMAGES_DIR / stem[:6]
    if not img_dir.is_dir():
        return []
    return sorted(p for p in img_dir.iterdir() if p.suffix.lower() in exts and not p.name.startswith("."))


def clean_body_text(text: str) -> str:
    """마크다운 → 네이버 SE3 붙여넣기용 평문 변환"""
    # 볼드
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    # 취소선
    text = re.sub(r'~~(.+?)~~', r'\1', text, flags=re.DOTALL)
    # 마크다운 테이블 → 탭 구분 텍스트 (SE3가 |를 취소선으로 인식하는 문제 방지)
    lines = text.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        # 구분선 행(|---|) 제거
        if re.match(r'^\|[-: |]+\|$', stripped):
            continue
        # 테이블 행 → 탭 구분
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [c.strip() for c in stripped[1:-1].split('|')]
            out.append('\t'.join(cells))
        else:
            out.append(line)
    return '\n'.join(out)


def _collapse_whitespace(text: str) -> str:
    return " ".join(str(text or "").split())


def _clip_context(text: str, limit: int = 220) -> str:
    compact = _collapse_whitespace(text)
    if len(compact) <= limit:
        return compact
    clipped = compact[:limit].rsplit(" ", 1)[0].strip()
    return clipped or compact[:limit]


async def _smart_editor_content_text(page: Page) -> str:
    script = """
        () => {
            const smart = window.SmartEditor || window.SE?.launcher;
            const editors = smart?._editors || {};
            const editor = editors.blogpc001 || Object.values(editors)[0];
            if (!editor?._documentService?.getContentText) return '';
            return String(editor._documentService.getContentText() || '');
        }
    """
    try:
        return await page.evaluate(script)
    except Exception:
        return ""


async def _editor_model_contains_text(page: Page, text: str) -> bool:
    snippet = ""
    for line in str(text or "").splitlines():
        compact = _collapse_whitespace(line)
        if compact:
            snippet = compact[:80]
            break
    if not snippet:
        snippet = _clip_context(text, 80)
    if not snippet:
        return True
    content = await _smart_editor_content_text(page)
    return _collapse_whitespace(content).find(_collapse_whitespace(snippet)) >= 0


async def _read_title_from_model(page: Page) -> str:
    """SmartEditor 내부 documentService 모델에서 제목 직접 읽기 (DOM 우회)"""
    script = """() => {
        const smart = window.SmartEditor || window.SE?.launcher;
        const editors = smart?._editors || {};
        const editor = editors.blogpc001 || Object.values(editors)[0];
        const service = editor?._documentService;
        if (!service?.getDocumentData) return '';
        try {
            const origGet = service.__blogAutoOriginalGetDocumentData || service.getDocumentData;
            const data = origGet.call(service);
            const components = data?.document?.components || [];
            const titleComp = components.find(c => c?.['@ctype'] === 'documentTitle') || components[0];
            return (titleComp?.title || [])
                .flatMap(p => p?.nodes || []).map(n => n?.value || '').join('').trim();
        } catch(e) { return ''; }
    }"""
    for ctx in [page] + [frame for frame in page.frames if frame != page.main_frame]:
        try:
            result = await ctx.evaluate(script)
            if result:
                return str(result).strip()
        except Exception:
            continue
    return ''


async def _smart_editor_document_serializes(page: Page) -> bool:
    script = """
        () => {
            const smart = window.SmartEditor || window.SE?.launcher;
            const editors = smart?._editors || {};
            const editor = editors.blogpc001 || Object.values(editors)[0];
            if (!editor?._documentService?.getDocumentData) return false;
            try {
                const data = editor._documentService.getDocumentData();
                return !!(data && data.document);
            } catch (e) {
                return false;
            }
        }
    """
    try:
        return bool(await page.evaluate(script))
    except Exception:
        return False


def _prepare_section_for_editor(text: str) -> str:
    """SE3 writeTextWithSoftLineBreak 전달 전 텍스트 정규화.
    ━━━ 구분선은 getContentText()에 반영 안 돼 probe 검증 실패 원인이므로 제거.
    ## 헤딩/마크다운/테이블 기호는 평문화하고 ~, | 는 완전 제거한다.
    """
    def clean_inline(value: str) -> str:
        value = re.sub(r'</?(?:s|strike|del)\b[^>]*>', '', value, flags=re.IGNORECASE)
        value = re.sub(r'~~(.+?)~~', r'\1', value, flags=re.DOTALL)
        value = re.sub(r'\*\*(.+?)\*\*', r'\1', value, flags=re.DOTALL)
        value = re.sub(r'__(.+?)__', r'\1', value, flags=re.DOTALL)
        return value.replace('~', '').replace('|', ' ')

    lines = text.replace('\r\n', '\n').replace('\r', '\n').splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[━─—\-=_\s]{3,}$', stripped):
            continue
        if re.match(r'^\|[-: |]+\|$', stripped):
            continue
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [clean_inline(c.strip()) for c in stripped[1:-1].split('|')]
            row = " / ".join(c for c in cells if c)
            if row:
                out.append(row)
            continue
        # 마크다운 헤딩은 '# 제목'처럼 # 뒤에 공백 필수(\s+).
        # 해시태그 줄 '#코스피마감 #코스닥마감...'은 # 뒤 공백이 없어 헤딩으로 오인되지 않음
        # (\s* 였을 때 첫 태그의 #만 잘려나가던 버그 수정)
        m = re.match(r'^#{1,6}\s+(\S.*)$', stripped)
        if m:
            out.append(clean_inline(m.group(1).strip()))
            continue
        if stripped.startswith('>'):
            stripped = re.sub(r'^>\s*', '', stripped)
        out.append(clean_inline(stripped))
    compact: list[str] = []
    blank = False
    for line in out:
        if line:
            compact.append(line)
            blank = False
        elif not blank:
            compact.append('')
            blank = True
    return '\n'.join(compact).strip()


async def _clear_active_editor_inline_formatting(page: Page) -> None:
    """현재 커서의 취소선/밑줄 상태가 켜져 있으면 끈다."""
    script = """() => {
        const isVisible = (el) => {
            if (!(el instanceof HTMLElement) || !el.isConnected) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        };
        const metaOf = (el) => [
            el.getAttribute('aria-label') || '',
            el.getAttribute('title') || '',
            el.getAttribute('data-name') || '',
            el.className || '',
            el.innerText || ''
        ].join(' ').toLowerCase();
        const changed = [];
        const buttons = Array.from(document.querySelectorAll(
            '.se-toolbar button, .se-toolbar [role="button"], .se-floating-toolbar button, button'
        ));
        for (const btn of buttons) {
            if (!isVisible(btn)) continue;
            const meta = metaOf(btn);
            const pressed = btn.getAttribute('aria-pressed') === 'true' ||
                /\b(se-btn--active|active|is-active)\b/.test(btn.className || '');
            const isDangerFormat = /(strike|line[- ]?through|취소선|underline|밑줄)/.test(meta);
            if (!pressed || !isDangerFormat) continue;
            try {
                btn.click();
                changed.push(meta.slice(0, 80));
            } catch (e) {}
        }
        if (document.queryCommandState && document.execCommand) {
            for (const cmd of ['strikeThrough', 'underline']) {
                try {
                    if (document.queryCommandState(cmd)) {
                        document.execCommand(cmd, false, null);
                        changed.push(`exec:${cmd}`);
                    }
                } catch (e) {}
            }
        }
        return changed;
    }"""
    changed_any: list[str] = []
    for ctx in [page] + [frame for frame in page.frames if frame != page.main_frame]:
        try:
            changed = await ctx.evaluate(script)
            if changed:
                changed_any.extend(str(item) for item in changed)
        except Exception:
            continue
    if changed_any:
        log.info(f"본문 인라인 서식 초기화: {', '.join(changed_any[:3])}")
        await asyncio.sleep(0.15)


async def _strip_strikethrough_from_editor(page: Page) -> int:
    """본문과 SE3 내부 발행 모델에 남은 취소선 서식을 제거한다."""
    model_script = """() => {
        const smart = window.SmartEditor || window.SE?.launcher;
        const editors = smart?._editors || {};
        const editor = editors.blogpc001 || Object.values(editors)[0];
        const documentService = editor?._documentService;
        if (!documentService?.getDocumentData || !documentService?.setDocumentData) {
            return 0;
        }
        const data = documentService.getDocumentData();
        let changed = 0;
        const cleanStyle = (obj) => {
            if (!obj || typeof obj !== 'object') return;
            const style = obj.style;
            if (style && typeof style === 'object') {
                for (const key of Object.keys(style)) {
                    const lowerKey = key.toLowerCase();
                    const lowerValue = String(style[key]).toLowerCase();
                    if (lowerKey.includes('strike') ||
                        lowerKey.includes('decoration') ||
                        lowerValue.includes('line-through')) {
                        delete style[key];
                        changed += 1;
                    }
                }
                const meaningful = Object.keys(style).filter((key) => key !== '@ctype');
                if (!meaningful.length) {
                    delete obj.style;
                    changed += 1;
                }
            }
            for (const value of Object.values(obj)) {
                if (Array.isArray(value)) {
                    value.forEach(cleanStyle);
                } else if (value && typeof value === 'object') {
                    cleanStyle(value);
                }
            }
        };
        cleanStyle(data);
        if (changed) {
            documentService.setDocumentData(data);
        }
        return changed;
    }"""
    dom_script = """() => {
        const rootSelectors = '.se-main-container, .se-document, [class*="se-main"], [class*="editorContent"]';
        const roots = Array.from(document.querySelectorAll(rootSelectors));
        if (!roots.length) {
            roots.push(...Array.from(document.querySelectorAll(
                '.se-component, .se-text-paragraph, [contenteditable="true"]'
            )).filter((el) => !el.closest('.se-title-input,.se-title-text')));
        }
        if (!roots.length) return 0;
        const seen = new Set();
        const isUI = (el) => {
            if (!(el instanceof Element)) return false;
            if (el.closest('button,[role="button"],input,select,textarea,option')) return true;
            if (el.closest('.se-toolbar,.se-floating-toolbar,.se-insert-menu,.se-help,.se-popup,.se-title-input,.se-title-text')) return true;
            return false;
        };
        let changed = 0;
        const touched = new Set();
        for (const root of roots) {
            if (!(root instanceof Element) || seen.has(root)) continue;
            seen.add(root);
            root.querySelectorAll('s, del, strike').forEach((el) => {
                if (isUI(el)) return;
                const text = document.createTextNode(el.textContent || '');
                el.replaceWith(text);
                changed += 1;
            });
            root.querySelectorAll('[style*="line-through"], [style*="text-decoration"]').forEach((el) => {
                if (!(el instanceof HTMLElement) || isUI(el)) return;
                const styleText = `${el.style.textDecoration || ''} ${el.style.textDecorationLine || ''}`.toLowerCase();
                if (!styleText.includes('line-through')) return;
                el.style.textDecoration = (el.style.textDecoration || '').replace(/line-through/ig, '').trim();
                el.style.textDecorationLine = (el.style.textDecorationLine || '').replace(/line-through/ig, '').trim();
                changed += 1;
            });
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (!node.nodeValue || !node.nodeValue.includes('~')) continue;
                if (isUI(node.parentElement)) continue;
                node.nodeValue = node.nodeValue.replace(/~/g, '');
                changed += 1;
            }
        }
        if (changed) {
            document.querySelectorAll('[contenteditable="true"]').forEach((el) => {
                if (!(el instanceof HTMLElement) || isUI(el) || touched.has(el)) return;
                touched.add(el);
                try { el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: '' })); } catch (e) {}
                try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
            });
        }
        return changed;
    }"""
    total = 0
    for ctx in [page] + [frame for frame in page.frames if frame != page.main_frame]:
        try:
            total += int(await asyncio.wait_for(ctx.evaluate(model_script), timeout=3.0) or 0)
        except Exception:
            continue
    for ctx in [page] + [frame for frame in page.frames if frame != page.main_frame]:
        try:
            total += int(await asyncio.wait_for(ctx.evaluate(dom_script), timeout=3.0) or 0)
        except Exception:
            continue
    return total


async def _insert_text_via_editor_model(page: Page, text: str) -> bool:
    """SE3 내부 API 직접 호출 — AutoFormat(취소선 등) 완전 우회."""
    if not _collapse_whitespace(text):
        return True

    await _clear_active_editor_inline_formatting(page)
    removed_before = await _strip_strikethrough_from_editor(page)
    if removed_before:
        log.warning(f"SmartEditor 모델 입력 전 취소선 내부 상태 {removed_before}개 제거")
    payload = {"text": text, "probe": _collapse_whitespace(text)[:80]}
    script = """
        (payload) => {
            const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const smart = window.SmartEditor || window.SE?.launcher;
            const editors = smart?._editors || {};
            const editor = editors.blogpc001 || Object.values(editors)[0];
            if (!editor?._editingService || !editor?._documentService) {
                return { ok: false, reason: 'no-editor' };
            }
            const editing = editor._editingService;
            const documentService = editor._documentService;
            const before = String(documentService.getContentText?.() || '');
            let beforeDocumentLength = 0;
            try {
                beforeDocumentLength = JSON.stringify(documentService.getDocumentData?.() || {}).length;
            } catch (e) {}
            try {
                if (editing.isBlockMode?.()) { editing.eraseBlock?.(); }
            } catch (e) {}
            try { editing.insertTextCompAtLast?.(); } catch (e) {}
            try {
                editing.writeTextWithSoftLineBreak(String(payload.text || ''));
            } catch (e) {
                try {
                    editing.write(String(payload.text || ''));
                } catch (inner) {
                    return { ok: false, reason: String(inner || e), before };
                }
            }
            const after = String(documentService.getContentText?.() || '');
            let afterDocumentLength = 0;
            try {
                afterDocumentLength = JSON.stringify(documentService.getDocumentData?.() || {}).length;
            } catch (e) {}
            const needle = normalize(payload.probe);
            const textChanged = after.length > before.length;
            const documentChanged = afterDocumentLength > beforeDocumentLength;
            return {
                ok: !needle || normalize(after).includes(needle) || textChanged || documentChanged,
                beforeLength: before.length,
                afterLength: after.length,
                beforeDocumentLength,
                afterDocumentLength,
            };
        }
    """
    try:
        result = await page.evaluate(script, payload)
    except Exception as exc:
        log.warning(f"SmartEditor 모델 입력 실패: {exc}")
        return False

    if isinstance(result, dict) and result.get("ok"):
        await asyncio.sleep(0.25)
        removed = await _strip_strikethrough_from_editor(page)
        if removed:
            log.warning(f"SmartEditor 모델 입력 후 취소선 잔재 {removed}개 제거")
        return True

    reason = result.get("reason") if isinstance(result, dict) else result
    log.warning(f"SmartEditor 모델 입력 미반영: {reason}")
    return False


def move_to_done(path: Path):
    dest = DONE_DIR / path.name
    shutil.move(str(path), str(dest))
    log.info(f"이동 완료: pending → done/{path.name}")


# ── 이미지 업로드 (blog_auto 검증 로직 이식) ──

def _editor_contexts(page: Page):
    return [page] + [frame for frame in page.frames if frame != page.main_frame]


async def _refocus_body(page: Page) -> bool:
    """SE3 본문 클릭 이벤트 + 포커스 복원 (floating toolbar 활성화 포함)"""
    _REFOCUS_JS = """() => {
        const fire = (el, type, extra={}) => {
            const rect = el.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            el.dispatchEvent(new MouseEvent(type, {
                bubbles: true, cancelable: true, view: window,
                clientX: x, clientY: y, ...extra
            }));
        };
        // 마지막 텍스트 컴포넌트 클릭 → SE3 floating toolbar 활성화
        const comps = Array.from(document.querySelectorAll('.se-component'));
        const textComps = comps.filter(c => (c.className||'').includes('se-text'));
        const last = textComps[textComps.length - 1];
        if (last) {
            const ce = last.querySelector('[contenteditable="true"]') || last;
            fire(ce, 'mousedown'); fire(ce, 'mouseup'); fire(ce, 'click');
            try { ce.focus(); } catch(e) {}
            return 'text-component';
        }
        // fallback: 제목 외 contenteditable 중 마지막
        const ces = Array.from(document.querySelectorAll('[contenteditable="true"]'))
            .filter(el => !el.closest('.se-title-input') && !el.closest('.se-title-text'));
        if (ces.length) {
            const ce = ces[ces.length - 1];
            fire(ce, 'mousedown'); fire(ce, 'mouseup'); fire(ce, 'click');
            try { ce.focus(); } catch(e) {}
            return 'fallback';
        }
        return null;
    }"""
    for frame in page.frames:
        try:
            result = await frame.evaluate(_REFOCUS_JS)
            if result:
                log.debug(f"본문 포커스+클릭 복원: {result}")
                return True
        except Exception:
            continue
    return False


async def _playwright_click_body(page: Page) -> bool:
    """Playwright 실제 클릭으로 SE3 본문 활성화 (floating toolbar 확실히 활성화)"""
    selectors = [
        ".se-component.se-text:last-child [contenteditable]:not([contenteditable='false'])",
        ".se-component.se-text [contenteditable]:not([contenteditable='false'])",
        ".se-component:last-child [contenteditable]:not([contenteditable='false'])",
        "[contenteditable]:not([contenteditable='false']):not(.se-title-input *):not(.se-title-text)",
    ]
    for ctx in _editor_contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).last
                if await loc.count() > 0 and await loc.is_visible(timeout=500):
                    await loc.click(timeout=3000)
                    log.debug(f"본문 Playwright 클릭: {sel}")
                    return True
            except Exception:
                continue
    return False


async def _count_image_components(page: Page) -> int:
    total = 0
    for ctx in _editor_contexts(page):
        try:
            total += int(await ctx.evaluate("""
                () => Array.from(document.querySelectorAll('.se-component'))
                    .filter((el) => (el.className || '').includes('se-image'))
                    .length
            """))
        except Exception:
            continue
    return total


async def _click_image_upload_button_anywhere(page: Page):
    script = """() => {
        const click = (btn, label) => {
            btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            btn.dispatchEvent(new MouseEvent('mouseup',   { bubbles: true, cancelable: true, view: window }));
            btn.dispatchEvent(new MouseEvent('click',     { bubbles: true, cancelable: true, view: window }));
            return label;
        };
        const container = document.querySelector('.se-main-container');
        if (!container) return null;  // se-main-container 없으면 에디터 밖 클릭 방지
        const selectors = [
            'button.se-image-toolbar-button',
            'button.se-insert-menu-button-image',
            'button[data-name="image"]',
            'button[data-se-menu-name="image"]',
            'button[data-se-menu-name="photo"]',
            'button[aria-label*="사진"]',
            'button[title*="사진"]',
            'button[aria-label*="이미지"]',
            'button[title*="이미지"]',
        ];
        for (const sel of selectors) {
            const btn = container.querySelector(sel);
            if (btn) return click(btn, sel);
        }
        const isVisible = (el) => {
            if (!(el instanceof HTMLElement) || !el.isConnected) return false;
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
        };
        for (const btn of container.querySelectorAll('.se-toolbar button,.se-floating-toolbar button,.se-insert-menu button,button,[role="button"]')) {
            if (!isVisible(btn)) continue;
            const meta = [
                btn.className||'',
                btn.getAttribute('aria-label')||'',
                btn.getAttribute('title')||'',
                btn.getAttribute('data-name')||'',
                btn.getAttribute('data-se-menu-name')||'',
                btn.innerText||''
            ].join(' ').toLowerCase();
            if (/글감|floating-category|search/.test(meta)) continue;
            if (/image|photo|picture|사진|이미지/.test(meta)) return click(btn, meta.slice(0, 80));
        }
        return null;
    }"""
    for idx, ctx in enumerate(_editor_contexts(page)):
        try:
            clicked = await ctx.evaluate(script)
            if clicked:
                return f"{clicked}@ctx{idx}"
        except Exception:
            continue
    return None


async def _click_image_upload_button(page: Page):
    # .se-main-container 스코핑으로 에디터 외부 링크 클릭 방지
    selectors = [
        ".se-main-container button.se-image-toolbar-button",
        ".se-main-container .se-image-toolbar-button",
        ".se-main-container button.se-insert-menu-button-image",
        ".se-main-container .se-insert-menu-button-image",
        ".se-main-container button[data-se-menu-name='image']",
        ".se-main-container button[data-se-menu-name='photo']",
        ".se-main-container button[data-name='image']",
    ]
    for idx, ctx in enumerate(_editor_contexts(page)):
        # get_by_role 은 frame(iframe) 에서만 — page 레벨에서는 에디터 밖 버튼 오클릭 위험
        if ctx != page:
            for name in ["사진", "이미지"]:
                try:
                    btn = ctx.get_by_role("button", name=name, exact=False)
                    if await btn.count() > 0 and await btn.first.is_visible(timeout=300):
                        await btn.first.click(timeout=3000)
                        return f"get_by_role(button,{name})@frame{idx}"
                except Exception:
                    continue
        for sel in selectors:
            try:
                loc = ctx.locator(sel)
                for pos in range(await loc.count()):
                    btn = loc.nth(pos)
                    if await btn.is_visible(timeout=300):
                        await btn.click(timeout=3000)
                        return f"{sel}@ctx{idx}"
            except Exception:
                continue
    return await _click_image_upload_button_anywhere(page)


async def _click_se3_insert_photo(page: Page) -> bool:
    """SE3 + 플로팅 버튼 → 사진 메뉴 2단계 흐름 (빈 줄에 커서 있을 때 동작)"""
    plus_selectors = [
        ".se-main-container button.se-toolbar-button-plus",
        ".se-main-container .se-floating-toolbar button",
        ".se-main-container button[class*='addComponent']",
        ".se-main-container button[class*='add_component']",
        ".se-main-container button[class*='plusButton']",
        ".se-main-container button[class*='plus_button']",
        ".se-main-container button[aria-label*='추가']",
        ".se-main-container button[aria-label*='insert']",
    ]
    plus_clicked = False
    for idx, ctx in enumerate(_editor_contexts(page)):
        for sel in plus_selectors:
            try:
                loc = ctx.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible(timeout=300):
                    await loc.first.click(timeout=2000)
                    plus_clicked = True
                    break
            except Exception:
                continue
        if plus_clicked:
            break
        for name in ["+", "추가", "구성요소 추가"]:
            try:
                btn = ctx.get_by_role("button", name=name, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible(timeout=300):
                    await btn.first.click(timeout=2000)
                    plus_clicked = True
                    break
            except Exception:
                continue
        if plus_clicked:
            break

    if not plus_clicked:
        return False

    await asyncio.sleep(0.8)

    photo_selectors = [
        ".se-main-container button[data-se-menu-name='image']",
        ".se-main-container button[data-se-menu-name='photo']",
        ".se-popup button[class*='image']",
        ".se-popup button[class*='photo']",
        ".se-main-container .se-insert-menu button[class*='image']",
        ".se-main-container .se-insert-menu button[class*='photo']",
    ]
    for idx, ctx in enumerate(_editor_contexts(page)):
        for name in ["사진", "이미지"]:
            try:
                btn = ctx.get_by_role("button", name=name, exact=False)
                if await btn.count() > 0 and await btn.first.is_visible(timeout=500):
                    await btn.first.click(timeout=2000)
                    return True
            except Exception:
                continue
        for sel in photo_selectors:
            try:
                loc = ctx.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible(timeout=500):
                    await loc.first.click(timeout=2000)
                    return True
            except Exception:
                continue
    return False


async def _set_file_input_anywhere(page: Page, img_path: Path) -> bool:
    for _ in range(20):
        # page 전체 탐색 우선
        try:
            file_inputs = page.locator("input[type='file']")
            if await file_inputs.count() > 0:
                await file_inputs.first.set_input_files(str(img_path))
                return True
        except Exception:
            pass
        # editor contexts 개별 탐색
        for ctx in _editor_contexts(page):
            try:
                file_inputs = ctx.locator("input[type='file']")
                if await file_inputs.count() > 0:
                    await file_inputs.first.set_input_files(str(img_path))
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.3)
    return False


async def _click_pc_upload_panel(page: Page) -> bool:
    labels = ["내 PC에서 불러오기", "내 PC", "PC에서 불러오기", "파일 선택", "사진 추가"]
    for ctx in _editor_contexts(page):
        for label in labels:
            try:
                btn = ctx.get_by_text(label, exact=True).first
                if await btn.is_visible(timeout=350):
                    await btn.click()
                    await asyncio.sleep(0.7)
                    return True
            except Exception:
                continue
    return False


async def _restore_editor_surface(page: Page) -> bool:
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    script = """() => {
        const isVisible = (el) => {
            if (!(el instanceof HTMLElement) || !el.isConnected) return false;
            const s = window.getComputedStyle(el);
            if (s.display==='none'||s.visibility==='hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width>0 && r.height>0 && r.bottom>0 && r.top<window.innerHeight;
        };
        const components = Array.from(document.querySelectorAll('.se-main-container .se-component,.se-component'))
            .filter((el) => el instanceof HTMLElement && !el.closest('.se-title-input'));
        const textComponents = components.filter((el) => (el.className||'').includes('se-text'));
        const target = textComponents[textComponents.length-1] || components[components.length-1] ||
            document.querySelector('.se-main-container,.se-document,.se-content');
        if (target instanceof HTMLElement) {
            try { target.scrollIntoView({block:'center',inline:'nearest'}); } catch(e){}
        }
        const clickTargets = [
            ...(target instanceof HTMLElement ? Array.from(target.querySelectorAll('.se-text-paragraph,.__se-node,[contenteditable="true"]')) : []),
            ...Array.from(document.querySelectorAll('.se-component.se-text .se-text-paragraph,.se-component.se-text .__se-node')).reverse(),
        ];
        for (const el of clickTargets) {
            if (!(el instanceof HTMLElement)||!isVisible(el)||el.closest('.se-title-input')) continue;
            try {
                el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));
                el.dispatchEvent(new MouseEvent('mouseup',  {bubbles:true,cancelable:true,view:window}));
                el.dispatchEvent(new MouseEvent('click',    {bubbles:true,cancelable:true,view:window}));
                try { el.focus?.(); } catch(e){}
                return true;
            } catch(e){}
        }
        return !!(target instanceof HTMLElement);
    }"""
    try:
        restored = bool(await page.evaluate(script))
    except Exception:
        restored = False
    await asyncio.sleep(0.25)
    return restored


async def _dismiss_transfer_error_popup(page: Page) -> bool:
    """파일 전송 오류 팝업 감지 및 확인 버튼 클릭. 팝업이 있으면 True 반환."""
    try:
        # JS로 팝업 텍스트 확인 후 확인 버튼 클릭
        result = await page.evaluate("""() => {
            const popups = Array.from(document.querySelectorAll('.se-popup, [role="dialog"], .ly_alert'));
            for (const p of popups) {
                const text = (p.innerText || p.textContent || '');
                if (text.includes('파일 전송') || text.includes('오류') || text.includes('다시 시도')) {
                    const btn = p.querySelector('button');
                    if (btn) { btn.click(); return true; }
                }
            }
            return false;
        }""")
        if result:
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass
    # Playwright 폴백
    try:
        btn = page.get_by_role("button", name="확인", exact=True)
        if await btn.count() > 0 and await btn.first.is_visible(timeout=500):
            await btn.first.click()
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass
    return False


async def _reload_and_restore_draft(page: Page) -> bool:
    """이미지 업로드 실패 후 페이지 새로고침 → 임시저장 초안 복원"""
    log.info("페이지 새로고침 중...")
    try:
        await page.reload(wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        log.warning(f"새로고침 실패: {e}")
        return False

    await asyncio.sleep(2)

    _EDITOR_SEL = ".se-title-text, .se-title-input [contenteditable], .se-title-input"
    _RESTORE_JS = """() => {
        const isVisible = (el) => {
            if (!(el instanceof HTMLElement) || !el.isConnected) return false;
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            return !!(el.offsetWidth || el.offsetHeight);
        };
        for (const btn of document.querySelectorAll('button, [role="button"]')) {
            if (!isVisible(btn)) continue;
            const t = (btn.innerText || btn.textContent || '').trim();
            if (/이어서\\s*작성|이어쓰기|계속\\s*작성/.test(t)) { btn.click(); return t; }
        }
        return null;
    }"""

    for _ in range(15):
        for ctx in [page] + page.frames:
            try:
                result = await ctx.evaluate(_RESTORE_JS)
                if result:
                    log.info(f"초안 복원 클릭: {result}")
                    await asyncio.sleep(2)
                    try:
                        await page.wait_for_selector(_EDITOR_SEL, state="visible", timeout=15000)
                    except Exception:
                        pass
                    return True
            except Exception:
                continue
        try:
            if await page.is_visible(_EDITOR_SEL, timeout=400):
                log.info("에디터 직접 로드 (팝업 없음)")
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)

    log.warning("새로고침 후 에디터 로드 미확인")
    return False


async def upload_image(page: Page, img_path: Path):
    """SE3 에디터에 이미지 업로드 — 파일 전송 오류 시 최대 3회 재시도"""
    _BLOCK_FILE_CLICK = """() => {
        if (!HTMLInputElement.prototype._origClick) {
            HTMLInputElement.prototype._origClick = HTMLInputElement.prototype.click;
        }
        HTMLInputElement.prototype.click = function() {
            if (this.type === 'file') return;
            HTMLInputElement.prototype._origClick.call(this);
        };
    }"""
    _RESTORE_FILE_CLICK = """() => {
        if (HTMLInputElement.prototype._origClick) {
            HTMLInputElement.prototype.click = HTMLInputElement.prototype._origClick;
            delete HTMLInputElement.prototype._origClick;
        }
        document.querySelectorAll('.se-popup-dim,.se-dialog,.se-modal').forEach(e=>e.remove());
    }"""

    last_error: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            log.info(f"이미지 업로드 재시도 ({attempt}/2): {img_path.name} — 7초 대기")
            await asyncio.sleep(7.0)
            if attempt == 1:
                await _reload_and_restore_draft(page)

        try:
            await page.evaluate("document.querySelectorAll('.se-popup-dim, .se-popup-alert, .se-popup-alert-confirm').forEach(e=>e.remove())")
        except Exception:
            pass
        image_count_before = await _count_image_components(page)
        uploaded = False

        try:
            for ctx in _editor_contexts(page):
                try:
                    await ctx.evaluate(_BLOCK_FILE_CLICK)
                except Exception:
                    pass
            try:
                await page.evaluate(_BLOCK_FILE_CLICK)
            except Exception:
                pass

            await _refocus_body(page)
            await asyncio.sleep(0.5)

            # 빈 단락 생성 → SE3 floating toolbar 활성화
            # JS로 마지막 텍스트 컴포넌트에 커서 이동 후 Enter (Playwright click 사용 안 함)
            _body_clicked = await page.evaluate("""() => {
                const comps = Array.from(document.querySelectorAll('.se-component.se-text [contenteditable="true"]'));
                const last = comps[comps.length - 1];
                if (!last) return false;
                last.focus();
                const r = document.createRange();
                r.selectNodeContents(last);
                r.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(r);
                last.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                return true;
            }""")
            if _body_clicked:
                await asyncio.sleep(0.3)
                try:
                    await page.keyboard.press("End")
                    await asyncio.sleep(0.15)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.6)
                except Exception:
                    pass
            else:
                # fallback: mouse.click on last visible contenteditable
                try:
                    loc = page.locator(".se-component.se-text [contenteditable='true']").last
                    if await loc.count() > 0:
                        bb = await loc.bounding_box()
                        if bb:
                            await page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("End")
                            await asyncio.sleep(0.15)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.6)
                            _body_clicked = True
                except Exception:
                    pass
                if not _body_clicked:
                    await asyncio.sleep(0.5)

            # 이미지 버튼이 나타날 때까지 최대 5초 대기 (에디터 초기화 타이밍 문제 방지)
            _IMG_BTN_SEL = ".se-main-container button.se-image-toolbar-button"
            for _w in range(10):
                _btn_found = False
                for _ctx in _editor_contexts(page):
                    try:
                        if await _ctx.locator(_IMG_BTN_SEL).count() > 0:
                            _btn_found = True
                            break
                    except Exception:
                        pass
                if _btn_found:
                    break
                await asyncio.sleep(0.5)

            # floating toolbar 먼저 시도 (매번 성공하는 경로)
            float_ok = await _click_se3_insert_photo(page)
            if float_ok:
                clicked = "floating_toolbar"
            else:
                clicked = await _click_image_upload_button(page)
                if not clicked:
                    await _restore_editor_surface(page)
                    await asyncio.sleep(0.5)
                    clicked = await _click_image_upload_button(page)

            if clicked:
                log.info(f"사진 버튼 클릭: {clicked}")
                await asyncio.sleep(2.0)
            else:
                log.warning(f"사진 버튼 클릭 실패 — file input 직접 접근 시도: {img_path.name}")

            # 사진 버튼 클릭 후 file input이 DOM에 붙는 데 시간이 걸림 →
            # 짧게 끊지 말고 최대 ~9초까지 점진 대기 (1차 시도 실패 후 새로고침 40초 낭비 방지)
            input_set = False
            for wait_sec in (0.15, 0.35, 0.5, 0.7, 1.0, 1.0, 1.5, 1.5, 2.0):
                if await _set_file_input_anywhere(page, img_path):
                    input_set = True
                    break
                await asyncio.sleep(wait_sec)
            if not input_set:
                await _click_pc_upload_panel(page)
                for wait_sec in (0.2, 0.7, 1.2, 1.5, 2.0):
                    if await _set_file_input_anywhere(page, img_path):
                        input_set = True
                        break
                    await asyncio.sleep(wait_sec)
            if not input_set:
                await page.screenshot(path=str(LOG_DIR / f"img_upload_fail_{img_path.stem}.png"))
                raise RuntimeError(f"이미지 file input 없음: {img_path.name}")

            log.info(f"파일 설정: {img_path.name}")
            for _ in range(24):
                if await _count_image_components(page) > image_count_before:
                    uploaded = True
                    log.info(f"이미지 업로드 완료: {img_path.name}")
                    break
                await asyncio.sleep(0.5)

            if not uploaded:
                raise RuntimeError(f"이미지 컴포넌트 증가 미확인: {img_path.name}")

        except RuntimeError as e:
            last_error = e
            log.warning(f"이미지 업로드 실패 (시도 {attempt+1}/3): {img_path.name} ({e})")
            # 파일 전송 오류 팝업 확인 및 닫기
            await _dismiss_transfer_error_popup(page)
            continue
        except Exception as e:
            last_error = e
            log.error(f"이미지 업로드 실패: {img_path.name} ({e})")
            raise
        finally:
            for ctx in _editor_contexts(page):
                try:
                    await ctx.evaluate(_RESTORE_FILE_CLICK)
                except Exception:
                    pass
            try:
                await page.evaluate(_RESTORE_FILE_CLICK)
            except Exception:
                pass
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        if uploaded:
            break

    if not uploaded:
        log.error(f"이미지 업로드 최종 실패 (3회 시도): {img_path.name}")
        raise RuntimeError(f"이미지 업로드 실패 (3회): {img_path.name}") from last_error

    await asyncio.sleep(1.0)
    for _ in range(3):
        try:
            await page.keyboard.press("ArrowDown")
        except Exception:
            break
        await asyncio.sleep(0.15)


# ── HTML 클립보드 붙여넣기 (blog_auto 검증 방식) ──

def _image_to_data_url(path: Path) -> str | None:
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if im.width > 1000:
                ratio = 1000 / im.width
                im = im.resize((1000, int(im.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning(f"이미지 변환 실패: {Path(path).name} ({e})")
        return None


def _inline_format(raw: str) -> str:
    """raw 텍스트 → HTML safe 문자열 (볼드 변환, 취소선 제거, HTML escape 포함)"""
    raw = re.sub(r'~~(.+?)~~', r'\1', raw)   # ~~취소선~~ 제거
    raw = re.sub(r'~(.+?)~', r'\1', raw)     # ~취소선~ 제거 (SE3 단일 틸드)
    raw = raw.replace('~', '')               # 홀로 남은 ~ 완전 제거 (SE3 취소선 트리거 방지)
    parts = re.split(r'\*\*', raw)
    out = []
    for i, part in enumerate(parts):
        escaped = html_mod.escape(part)
        out.append(f"<strong>{escaped}</strong>" if i % 2 == 1 else escaped)
    return ''.join(out)


def _markdown_table_to_html(lines: list[str]) -> str:
    """마크다운 테이블 → HTML table 태그 (| 문자 제거로 취소선 방지)"""
    rows = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^\|[-: |]+\|$', stripped):  # 구분선 행 스킵
            continue
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [c.strip() for c in stripped[1:-1].split('|')]
            rows.append(cells)
    if not rows:
        return ''
    tbl = '<table style="border-collapse:collapse;width:100%;margin:12px 0">'
    for i, row in enumerate(rows):
        tbl += '<tr>'
        tag = 'th' if i == 0 else 'td'
        for cell in row:
            style = 'padding:6px 10px;border:1px solid #ccc;text-align:left'
            if i == 0:
                style += ';background:#f5f5f5;font-weight:bold'
            tbl += f'<{tag} style="{style}">{_inline_format(cell)}</{tag}>'
        tbl += '</tr>'
    tbl += '</table>'
    return tbl


def _text_section_to_fragment(text: str) -> str:
    """텍스트 섹션 -> HTML fragment (CF_HTML clipboard 삽입용).
    -, ~, |, ━ 등 취소선 유발 문자를 HTML 태그로 변환하거나 제거한다.
    반환값: <html><body><!--StartFragment-->HERE<!--EndFragment--> 에 들어갈 내용.
    """
    pieces: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue

        # ① 수평 구분선 (━━━ / --- / ===) → 제거
        if all(re.match(r'^[━─\-=_\s]{3,}$', l) for l in lines):
            continue

        # ② 마크다운 테이블 (| 문자 완전 제거)
        if any(l.startswith('|') and l.endswith('|') for l in lines):
            pieces.append(_markdown_table_to_html(lines))
            continue

        # ③ 헤딩 ## / ###
        if len(lines) == 1 and re.match(r'^#{1,3}\s', lines[0]):
            content = re.sub(r'^#+\s*', '', lines[0])
            pieces.append(f'<h3 style="margin:24px 0 10px;font-size:19px">{_inline_format(content)}</h3>')
            continue

        # ④ 인용구 > 로 시작
        if lines[0].startswith('>'):
            inner_lines = [re.sub(r'^>\s*["""]?', '', l).rstrip('"""') for l in lines]
            inner = '<br>'.join(_inline_format(l) for l in inner_lines if l.strip())
            if inner:
                pieces.append(
                    f'<blockquote style="border-left:3px solid #ccc;'
                    f'margin:0 0 12px;padding:8px 16px;color:#555;font-style:italic">{inner}</blockquote>'
                )
            continue

        # ⑤ 불릿 혼합 블록 (▶ 헤더 + - 불릿 같은 블록도 분리 처리)
        bullet_idx = [i for i, l in enumerate(lines) if re.match(r'^-\s', l)]
        if bullet_idx:
            non_bullet = [lines[i] for i in range(len(lines)) if i not in bullet_idx]
            if non_bullet:
                safe = [_inline_format(l) for l in non_bullet]
                pieces.append(f'<p style="margin:0 0 8px">{"<br>".join(safe)}</p>')
            items = [re.sub(r'^-\s*', '', lines[i]) for i in bullet_idx]
            lis = ''.join(f'<li style="margin:3px 0">{_inline_format(item)}</li>'
                          for item in items if item.strip())
            if lis:
                pieces.append(f'<ul style="margin:0 0 12px;padding-left:20px">{lis}</ul>')
            continue

        # ⑥ 일반 단락
        safe_lines = [_inline_format(l) for l in lines]
        pieces.append(f'<p style="margin:0 0 12px">{"<br>".join(safe_lines)}</p>')

    return ''.join(pieces)


def _set_clipboard_html_and_text(html_fragment: str, plain_text: str) -> None:
    """CF_HTML + CF_UNICODETEXT 동시 설정.
    CF_HTML만 넣으면 Windows가 CF_UNICODETEXT를 자동 합성하는데,
    합성 결과에 HTML 태그 잔재가 섞일 수 있어서 직접 깨끗한 plain text를 같이 넣는다.
    SE3(Chromium)가 어느 쪽을 읽어도 취소선 트리거 문자가 없도록 보장.
    """
    import ctypes

    cf_html = ctypes.windll.user32.RegisterClipboardFormatW("HTML Format")
    CF_UNICODETEXT = 13

    pre  = "<html><body><!--StartFragment-->"
    post = "<!--EndFragment--></body></html>"

    header_placeholder = (
        "Version:0.9\r\n"
        "StartHTML:0000000000\r\n"
        "EndHTML:0000000000\r\n"
        "StartFragment:0000000000\r\n"
        "EndFragment:0000000000\r\n"
    )
    hlen       = len(header_placeholder.encode("utf-8"))
    frag_enc   = html_fragment.encode("utf-8")
    start_html = hlen
    start_frag = start_html + len(pre.encode("utf-8"))
    end_frag   = start_frag + len(frag_enc)
    end_html   = end_frag  + len(post.encode("utf-8"))

    header = (
        "Version:0.9\r\n"
        f"StartHTML:{start_html:010d}\r\n"
        f"EndHTML:{end_html:010d}\r\n"
        f"StartFragment:{start_frag:010d}\r\n"
        f"EndFragment:{end_frag:010d}\r\n"
    )
    html_full = (header + pre + html_fragment + post).encode("utf-8") + b"\x00"
    text_full = (plain_text + "\x00").encode("utf-16-le")

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32

    k32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype   = ctypes.c_void_p
    k32.GlobalLock.argtypes   = [ctypes.c_void_p]
    k32.GlobalLock.restype    = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes   = [ctypes.c_void_p]
    k32.GlobalFree.restype    = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype  = ctypes.c_void_p
    u32.OpenClipboard.argtypes    = [ctypes.c_void_p]

    h_html = k32.GlobalAlloc(0x0002, len(html_full))
    if not h_html:
        raise RuntimeError("GlobalAlloc 실패 (CF_HTML)")
    p = k32.GlobalLock(h_html)
    if not p:
        k32.GlobalFree(h_html)
        raise RuntimeError("GlobalLock 실패 (CF_HTML)")
    ctypes.memmove(p, html_full, len(html_full))
    k32.GlobalUnlock(h_html)

    h_text = k32.GlobalAlloc(0x0002, len(text_full))
    if not h_text:
        k32.GlobalFree(h_html)
        raise RuntimeError("GlobalAlloc 실패 (CF_UNICODETEXT)")
    p = k32.GlobalLock(h_text)
    if not p:
        k32.GlobalFree(h_html)
        k32.GlobalFree(h_text)
        raise RuntimeError("GlobalLock 실패 (CF_UNICODETEXT)")
    ctypes.memmove(p, text_full, len(text_full))
    k32.GlobalUnlock(h_text)

    if not u32.OpenClipboard(None):
        k32.GlobalFree(h_html)
        k32.GlobalFree(h_text)
        raise RuntimeError("OpenClipboard 실패")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(cf_html, h_html):
            raise RuntimeError("SetClipboardData 실패 (CF_HTML)")
        if not u32.SetClipboardData(CF_UNICODETEXT, h_text):
            raise RuntimeError("SetClipboardData 실패 (CF_UNICODETEXT)")
    except Exception:
        k32.GlobalFree(h_html)
        k32.GlobalFree(h_text)
        raise
    finally:
        u32.CloseClipboard()

    log.debug(f"CF_HTML+CF_UNICODETEXT 클립보드 저장 완료 ({len(html_full)} bytes)")


async def _focus_body_safe(page: Page):
    """제목 영역 제외하고 본문만 포커스"""
    # SE3 본문 셀렉터 — 제목 입력(.se-title-input) 제외, 새 글/기존 글 모두 커버
    body_selectors = [
        ".se-text-paragraph",                           # 텍스트 있을 때
        ".se-component.se-text [contenteditable='true']",  # 텍스트 컴포넌트
        ".se-main-container .se-document [contenteditable='true']",  # 문서 전체
        ".se-component:not(.se-title) [contenteditable='true']",
    ]
    for sel in body_selectors:
        try:
            loc = page.locator(sel).last
            if await loc.is_visible(timeout=1000):
                bb = await loc.bounding_box()
                if bb:
                    await page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                    await asyncio.sleep(0.4)
                    return
        except Exception:
            continue
    # JS fallback: .se-main-container 안에서 .se-title-input 밖 첫 contenteditable
    focused = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('.se-main-container [contenteditable="true"]'));
        const body = all.find(el => !el.closest('.se-title-input'));
        if (body) {
            body.focus();
            const r = document.createRange();
            r.selectNodeContents(body);
            r.collapse(false);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(r);
            return true;
        }
        return false;
    }""")
    if focused:
        await asyncio.sleep(0.4)
        return
    # 최종 fallback: JS refocus (_playwright_click_body는 흰 화면 유발 금지)
    try:
        await _refocus_body(page)
        await asyncio.sleep(0.4)
    except Exception:
        pass


async def _paste_text_section_as_html(page: Page, text: str):
    """텍스트 섹션 → CF_HTML 클립보드 → Ctrl+V → 취소선 즉시 제거.
    CF_HTML paste가 SE3에서 텍스트를 가장 안정적으로 입력하는 방식.
    _inline_format에서 ~ 를 완전 제거했으므로 취소선 원인이 없음.
    혹시 모를 경우를 위해 붙여넣기 후 s/del/strike 태그를 즉시 제거.
    """
    html_fragment = _text_section_to_fragment(text)
    if not html_fragment.strip():
        return

    # plain text (CF_UNICODETEXT 동시 설정용)
    plain = re.sub(r'<[^>]+>', ' ', html_fragment)
    plain = re.sub(r'\s+', ' ', plain).strip()

    await _focus_body_safe(page)
    await asyncio.sleep(0.3)
    await _clear_active_editor_inline_formatting(page)
    _set_clipboard_html_and_text(html_fragment, plain)
    await page.keyboard.press("Control+V")
    await asyncio.sleep(1.5)

    # 붙여넣기 후 취소선 태그 즉시 제거 (방어적 조치)
    _STRIP_STRIKE_JS = """() => {
        const roots = [
            document.querySelector('.se-main-container'),
            document.querySelector('.se-document'),
        ].filter(Boolean);
        let removed = 0;
        for (const root of roots) {
            root.querySelectorAll('s, del, strike').forEach(el => {
                const t = document.createTextNode(el.textContent || '');
                el.replaceWith(t);
                removed++;
            });
        }
        return removed;
    }"""
    for ctx in _editor_contexts(page):
        try:
            n = await ctx.evaluate(_STRIP_STRIKE_JS)
            if n:
                log.info(f"취소선 태그 {n}개 즉시 제거")
        except Exception:
            continue
    removed = await _strip_strikethrough_from_editor(page)
    if removed:
        log.info(f"취소선 잔재 {removed}개 제거")


# ── Playwright 발행 ──

async def clear_popups(page: Page):
    """SE3 에디터 팝업·임시저장 팝업·작성중인글 팝업 닫기"""
    # 도움말 패널
    try:
        btn = page.locator(".se-help-panel-close-button").first
        if await btn.is_visible(timeout=2000):
            await btn.click()
            log.info("도움말 패널 닫음")
            await asyncio.sleep(1)
    except Exception:
        pass

    contexts = [("page", page)] + [(f"frame[{idx}]", frame) for idx, frame in enumerate(page.frames)]
    popup_found = False
    popup_buttons: list = []

    # 네이버 '작성 중인 글이 있습니다' 팝업 처리 (ib 스킬에서 검증된 방식)
    #   - '취소' = 새 글 작성(옛 초안 안 불러옴) → 우리가 원하는 동작
    #   - '확인'/'이어서 작성'/'계속' = 옛 초안 복원(덧붙음) → 절대 클릭 금지
    for attempt in range(3):
        for label, ctx in contexts:
            try:
                res = await ctx.evaluate("""
                    ({ markerTexts, freshTexts, restoreTexts }) => {
                        const isVisible = (el) => {
                            if (!(el instanceof HTMLElement) || !el.isConnected) return false;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        const textOf = (el) => ((el.innerText || el.textContent || '') + '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        const hasAny = (t, arr) => arr.some((m) => t.includes(m));
                        const containers = Array.from(document.querySelectorAll(
                            "[role='dialog'], .se-popup, .se-popup-container, .se-popup-alert, .se-popup-alert-confirm, .ly_alert, .layer_popup"
                        ));
                        const box = containers.find((el) => isVisible(el) && hasAny(textOf(el), markerTexts));
                        if (!box) return { found: false };

                        const buttons = Array.from(box.querySelectorAll("button, [role='button'], a")).filter(isVisible);
                        const labels = buttons.map(textOf);
                        const isRestore = (t) => hasAny(t, restoreTexts);
                        // 1순위: '새 글 작성' 계열
                        let target = buttons.find((b) => hasAny(textOf(b), freshTexts));
                        // 2순위: '취소'/'닫기' (= 새 글 작성), 단 '확인/이어서/계속'은 제외
                        if (!target) target = buttons.find((b) => {
                            const t = textOf(b);
                            return /취소|닫기|아니오|아니요/.test(t) && !isRestore(t);
                        });
                        if (target) {
                            const t = textOf(target);
                            target.click();
                            return { found: true, clicked: t, buttons: labels };
                        }
                        return { found: true, clicked: null, buttons: labels };
                    }
                """, {
                    "markerTexts": [
                        "작성 중인 글이 있습니다",
                        "작성중인 글이 있습니다",
                        "이어서 작성",
                        "저장된 글",
                        "임시저장",
                    ],
                    "freshTexts": ["새 글 작성", "새글 작성", "새 글 쓰기", "새로 작성", "새로 쓰기"],
                    "restoreTexts": ["확인", "이어서", "계속", "불러오기"],
                })
            except Exception:
                res = None

            if isinstance(res, dict) and res.get("found"):
                popup_found = True
                if res.get("buttons"):
                    popup_buttons = res["buttons"]
                if res.get("clicked"):
                    log.info(f"작성 중인 글 팝업 닫음 ({label}): '{res['clicked']}' 클릭 (버튼={popup_buttons})")
                    await asyncio.sleep(2.0)
                    return

        if popup_found:
            log.warning(f"작성 중인 글 팝업 감지, JS 닫기 실패 (버튼={popup_buttons}) — 재시도 ({attempt+1}/3)")
            await asyncio.sleep(0.5)
        else:
            break

    if popup_found:
        # JS로 못 닫음 → Playwright로 '취소'/'닫기'/'새 글 작성' 직접 클릭 (ib 스킬 검증 방식)
        for txt in ["취소", "닫기", "새 글 작성", "새로 작성"]:
            try:
                btn = page.locator(f"button:has-text('{txt}')").first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await asyncio.sleep(1.5)
                    log.info(f"작성 중인 글 팝업 닫음(Playwright): '{txt}' 클릭")
                    return
            except Exception:
                continue
        # 그래도 안 닫히면 감지된 팝업 컨테이너 자체를 DOM에서 제거 → 빈 에디터로 진행
        try:
            removed = await page.evaluate("""
                (markerTexts) => {
                    const textOf = (el) => ((el.innerText || el.textContent || '') + '').replace(/\\s+/g, ' ').trim();
                    const sel = "[role='dialog'], .se-popup, .se-popup-container, .se-popup-alert, .se-popup-alert-confirm, .ly_alert, .layer_popup";
                    let n = 0;
                    document.querySelectorAll(sel).forEach((el) => {
                        if (markerTexts.some((m) => textOf(el).includes(m))) { el.remove(); n++; }
                    });
                    document.querySelectorAll('.se-popup-dim, .dimmed, .layer_dim').forEach((el) => { el.remove(); n++; });
                    return n;
                }
            """, ["작성 중인 글이 있습니다", "작성중인 글이 있습니다", "이어서 작성", "저장된 글", "임시저장"])
            log.info(f"작성 중인 글 팝업 강제 제거: {removed}개 (빈 에디터로 진행)")
            await asyncio.sleep(1.0)
        except Exception as e:
            log.warning(f"작성 중인 글 팝업 강제 제거 실패: {e}")
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:
            pass

    # dim/alert 레이어 제거 — .__se-sentry 제외 (툴바 버튼도 해당 클래스를 사용함)
    try:
        await page.evaluate("""
            document.querySelectorAll(
                '.se-popup-dim, .se-popup-alert, .se-popup-alert-confirm'
            ).forEach(el => el.remove())
        """)
    except Exception:
        pass


import ctypes as _ctypes

def set_clipboard(text: str) -> bool:
    """Windows ctypes로 클립보드에 UTF-16LE 복사"""
    try:
        if not _ctypes.windll.user32.OpenClipboard(None):
            return False
        _ctypes.windll.user32.EmptyClipboard()
        data = text.encode("utf-16le") + b"\x00\x00"
        h_mem = _ctypes.windll.kernel32.GlobalAlloc(0x0002, len(data))
        if not h_mem:
            _ctypes.windll.user32.CloseClipboard()
            return False
        ptr = _ctypes.windll.kernel32.GlobalLock(h_mem)
        if ptr:
            _ctypes.cdll.msvcrt.memcpy(ptr, data, len(data))
            _ctypes.windll.kernel32.GlobalUnlock(h_mem)
            _ctypes.windll.user32.SetClipboardData(13, h_mem)
        _ctypes.windll.user32.CloseClipboard()
        return True
    except Exception as e:
        log.error(f"클립보드 복사 오류: {e}")
        return False


async def _has_session(page: Page) -> bool:
    """현재 URL로 로그인 상태 확인 (쿠키는 만료돼도 있을 수 있어서 URL 우선)"""
    url = page.url
    if "nidlogin" in url or "nid.naver.com" in url:
        return False
    if "blog.naver.com" in url and "PostWriteForm" in url:
        return True
    # 애매한 경우 쿠키로 보조 확인
    try:
        cookies = await page.context.cookies()
        names = {c["name"] for c in cookies}
        return bool({"NID_SES", "NID_AUT"} & names)
    except Exception:
        return False


async def login(page: Page):
    """네이버 로그인 (세션이 살아있으면 스킵) — 세션 없으면 자동 입력 후 5분 대기"""
    log.info("세션 확인 중...")
    write_url = f"https://blog.naver.com/PostWriteForm.naver?blogId={BLOG_ID}"
    await page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if await _has_session(page):
        log.info("기존 세션 사용")
        return

    await page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if NAVER_ID and NAVER_PW:
        try:
            log.info("클립보드 방식으로 자동 로그인 시도...")
            await page.click("#id")
            await asyncio.sleep(0.5)
            if set_clipboard(NAVER_ID):
                await page.keyboard.press("Control+V")
            else:
                await page.fill("#id", NAVER_ID)
            await asyncio.sleep(0.5)

            await page.click("#pw")
            await asyncio.sleep(0.5)
            if set_clipboard(NAVER_PW):
                await page.keyboard.press("Control+V")
            else:
                await page.fill("#pw", NAVER_PW)
            await asyncio.sleep(0.5)

            login_btn = page.locator(".btn_login, #log\\.login, button[type='submit']").first
            await login_btn.click()
            log.info("로그인 버튼 클릭 완료")
            await asyncio.sleep(3)
        except Exception as e:
            log.error(f"자동 로그인 중 오류: {e}")
    else:
        log.warning("NAVER_ID/PW 미설정 — 수동 로그인 필요")

    log.warning("2차 인증 또는 캡차가 있으면 브라우저에서 직접 완료해 주세요 (최대 5분 대기)")
    import time as _time
    deadline = _time.time() + 300
    while _time.time() < deadline:
        await asyncio.sleep(3)
        if await _has_session(page):
            log.info("로그인 세션 확인 완료")
            return
    raise Exception("로그인 대기 시간 초과 (5분)")


TITLE_SELECTORS = [
    ".se-documentTitle [contenteditable]:not([contenteditable='false'])",
    ".se-title-input [contenteditable]:not([contenteditable='false'])",
    ".se-title-input [contenteditable]",
    ".se-title-text",
    "[placeholder*='제목']",
]
PLACEHOLDER_STRINGS = {"제목", "제목을 입력해 주세요"}


async def _patch_document_title_model(page: Page, title: str) -> bool:
    """SE3 SmartEditor 내부 documentService 모델에 제목을 직접 패치"""
    script = """
        (title) => {
            const smart = window.SmartEditor || window.SE?.launcher;
            const editors = smart?._editors || {};
            const editor = editors.blogpc001 || Object.values(editors)[0];
            const service = editor?._documentService;
            if (!service?.getDocumentData) return false;
            service.__blogAutoTitle = String(title || '');
            const patch = (data) => {
                const currentTitle = String(service.__blogAutoTitle || title || '');
                const components = data?.document?.components || [];
                const titleComp = components.find((comp) => comp?.['@ctype'] === 'documentTitle') || components[0];
                if (!titleComp) return data;
                if (!Array.isArray(titleComp.title) || !titleComp.title.length) {
                    titleComp.title = [{ id: `SE-title-${Date.now()}`, nodes: [], '@ctype': 'paragraph' }];
                }
                const paragraph = titleComp.title[0];
                if (!Array.isArray(paragraph.nodes) || !paragraph.nodes.length) {
                    paragraph.nodes = [{ id: `SE-title-node-${Date.now()}`, value: '', style: { '@ctype': 'nodeStyle' }, '@ctype': 'textNode' }];
                }
                paragraph.nodes[0].value = currentTitle;
                titleComp.align = titleComp.align || 'left';
                return data;
            };
            if (!service.__blogAutoOriginalGetDocumentData) {
                service.__blogAutoOriginalGetDocumentData = service.getDocumentData.bind(service);
                service.getDocumentData = function(...args) { return patch(service.__blogAutoOriginalGetDocumentData(...args)); };
            }
            try {
                const data = service.__blogAutoOriginalGetDocumentData
                    ? service.__blogAutoOriginalGetDocumentData() : service.getDocumentData();
                patch(data);
            } catch (e) {}
            // DOM 직접 업데이트 — 모델과 DOM을 동기화하여 검증 통과
            try {
                const titleP = document.querySelector('.se-title-text .se-text-paragraph')
                    || document.querySelector('.se-documentTitle P.se-text-paragraph')
                    || document.querySelector('.se-documentTitle p');
                if (titleP) {
                    let textSpan = Array.from(titleP.children)
                        .find(el => !el.classList.contains('se-placeholder')
                                 && !el.classList.contains('__se_placeholder'));
                    if (!textSpan) {
                        textSpan = document.createElement('span');
                        titleP.insertBefore(textSpan, titleP.firstChild);
                    }
                    textSpan.textContent = String(title || '');
                    titleP.querySelectorAll('.__se_placeholder, .se-placeholder')
                        .forEach(p => { p.style.display = 'none'; });
                    // se-is-empty 제거 (SE3 내부 검증이 이 클래스로 빈 제목 판단)
                    ['.se-title-text', '.se-documentTitle .se-module-text',
                     '.se-documentTitle', '.se-section-documentTitle'].forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.classList.remove('se-is-empty'));
                    });
                }
            } catch (domErr) {}
            try {
                const data = service.getDocumentData();
                const components = data?.document?.components || [];
                const titleComp = components.find((comp) => comp?.['@ctype'] === 'documentTitle') || components[0];
                const text = (titleComp?.title || [])
                    .flatMap((p) => p?.nodes || []).map((n) => n?.value || '').join('');
                return text === String(title || '');
            } catch (e) { return false; }
        }
    """
    try:
        return bool(await page.evaluate(script, title))
    except Exception:
        return False


async def _patch_se3_title_component(page: Page, title: str) -> bool:
    """SE3 setDocumentData + componentManager로 내부 모델에 직접 제목 주입 (blog_auto 검증 방식)"""
    script = """
        (title) => {
            const smart = window.SmartEditor || window.SE?.launcher;
            const editors = smart?._editors || {};
            const editor = editors.blogpc001 || Object.values(editors)[0];
            if (!editor) return { ok: false, reason: 'no_editor' };
            const tried = [];

            // Method 1: setDocumentData
            try {
                const service = editor._documentService;
                if (service) {
                    const origGet = service.__blogAutoOriginalGetDocumentData || service.getDocumentData?.bind(service);
                    const rawData = origGet?.() || service.getDocumentData?.();
                    if (rawData && service.setDocumentData) {
                        const components = rawData?.document?.components || [];
                        let titleComp = components.find(c => c?.['@ctype'] === 'documentTitle') || components[0];
                        if (titleComp) {
                            if (!Array.isArray(titleComp.title) || !titleComp.title.length) {
                                titleComp.title = [{ id: 'SE-title-auto', nodes: [], '@ctype': 'paragraph' }];
                            }
                            const para = titleComp.title[0];
                            if (!Array.isArray(para.nodes) || !para.nodes.length) {
                                para.nodes = [{ id: 'SE-title-node-auto', value: '', style: { '@ctype': 'nodeStyle' }, '@ctype': 'textNode' }];
                            }
                            para.nodes[0].value = title;
                            titleComp.align = titleComp.align || 'left';
                            service.setDocumentData(rawData);
                            tried.push('setDocumentData:ok');
                        } else { tried.push('setDocumentData:no_title_comp'); }
                    } else { tried.push('setDocumentData:no_method'); }
                }
            } catch(e) { tried.push('setDocumentData:err:' + String(e.message).substring(0,60)); }

            // Method 2: componentManager
            try {
                const cm = editor._componentManager || editor.componentManager;
                if (cm) {
                    const src = cm._componentMap || cm._components || (cm.getComponents?.() ?? {});
                    const comps = src instanceof Map ? Array.from(src.values()) : Object.values(src);
                    const titleComp = comps.find(c => {
                        if (!c) return false;
                        const ct = (c._ctype || c.ctype || c.type || c['@ctype'] || c.constructor?.name || '').toLowerCase();
                        return ct.includes('title') || ct.includes('documenttitle') ||
                               (c._el || c.el)?.classList?.contains('se-documentTitle');
                    });
                    if (titleComp) {
                        if (titleComp.setText) { titleComp.setText(title); tried.push('comp.setText:ok'); }
                        else if (titleComp.setTitle) { titleComp.setTitle(title); tried.push('comp.setTitle:ok'); }
                        else if (titleComp.setData) { titleComp.setData({ title }); tried.push('comp.setData:ok'); }
                        const model = titleComp._model || titleComp.model;
                        if (model) {
                            if (typeof model.set === 'function') { model.set('text', title); tried.push('model.set:ok'); }
                            else { model.text = title; tried.push('model.text=:ok'); }
                            if (typeof titleComp.render === 'function') titleComp.render();
                        }
                    } else { tried.push('comp_mgr:no_title_comp'); }
                } else { tried.push('comp_mgr:none'); }
            } catch(e) { tried.push('comp_mgr:err:' + String(e.message).substring(0,60)); }

            // Method 3: editing service / editor direct
            try {
                const es = editor._editingService || editor.editingService;
                if (es?.setDocumentTitle) { es.setDocumentTitle(title); tried.push('es.setDocumentTitle:ok'); }
                if (editor.setTitle) { editor.setTitle(title); tried.push('editor.setTitle:ok'); }
                if (editor.setDocumentTitle) { editor.setDocumentTitle(title); tried.push('editor.setDocumentTitle:ok'); }
            } catch(e) { tried.push('editor_direct:err'); }

            // DOM patch
            try {
                const titleP = document.querySelector('.se-title-text .se-text-paragraph')
                            || document.querySelector('.se-documentTitle P.se-text-paragraph')
                            || document.querySelector('.se-documentTitle p');
                if (titleP) {
                    let textSpan = Array.from(titleP.children).find(el =>
                        !el.classList.contains('se-placeholder') && !el.classList.contains('__se_placeholder'));
                    if (!textSpan) {
                        textSpan = document.createElement('span');
                        titleP.insertBefore(textSpan, titleP.firstChild);
                    }
                    textSpan.textContent = title;
                    titleP.querySelectorAll('.__se_placeholder, .se-placeholder').forEach(p => { p.style.display = 'none'; });
                    ['.se-title-text', '.se-documentTitle .se-module-text',
                     '.se-documentTitle', '.se-section-documentTitle'].forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.classList.remove('se-is-empty'));
                    });
                    tried.push('dom_patch:ok');
                } else { tried.push('dom_patch:no_p'); }
            } catch(e) { tried.push('dom_patch:err'); }

            const domTitle = (() => {
                const titleP = document.querySelector('.se-title-text .se-text-paragraph')
                            || document.querySelector('.se-documentTitle p');
                if (!titleP) return '';
                const spans = Array.from(titleP.children).filter(el =>
                    !el.classList.contains('se-placeholder') && !el.classList.contains('__se_placeholder'));
                return spans.map(s => (s.textContent || '').trim()).filter(Boolean).join('').trim();
            })();
            return { ok: domTitle === title, dom: domTitle.substring(0, 50), tried };
        }
    """
    try:
        result = await page.evaluate(script, title)
        log.info(f"[SE3-patch] tried={result.get('tried', [])}, dom='{result.get('dom', '')}', ok={result.get('ok', False)}")
        return bool(result.get("ok"))
    except Exception as e:
        log.info(f"[SE3-patch] 예외: {e}")
        return False


def _normalize(text: str) -> str:
    return " ".join(str(text or "").replace("​", " ").replace("\xa0", " ").split())


async def _read_title(page: Page) -> str:
    # 1순위: SmartEditor 내부 모델 (DOM re-render에 영향 없음)
    model_title = await _read_title_from_model(page)
    if model_title:
        return model_title
    # 2순위: DOM 셀렉터
    for sel in TITLE_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                try:
                    await el.evaluate("(n) => n.querySelectorAll?.('.__se_placeholder,.se-placeholder')?.forEach(p => p.style.display='none')")
                except Exception:
                    pass
                text = _normalize(await el.inner_text())
                if text and text not in PLACEHOLDER_STRINGS:
                    return text
        except Exception:
            continue
    return ""


async def _commit_title_input_state(page: Page, title: str) -> bool:
    """blog_auto.py에서 검증된 방식: 네이티브 키보드 이벤트 + JS 이벤트 + 모델 패치"""
    expected = _normalize(title)
    keyboard_ok = False

    for sel in [".se-title-input [contenteditable]", ".se-title-text", "[placeholder*='제목']"]:
        try:
            el = page.locator(sel).first
            if not await el.is_visible(timeout=1200):
                continue
            await el.click()
            await el.evaluate(
                """(node) => {
                    const target =
                        node?.querySelector?.("[contenteditable='true']") ||
                        node?.querySelector?.('.__se-node') ||
                        node;
                    if (!(target instanceof HTMLElement)) return false;
                    target.focus();
                    const range = document.createRange();
                    range.selectNodeContents(target);
                    const selection = window.getSelection();
                    selection?.removeAllRanges();
                    selection?.addRange(range);
                    return true;
                }"""
            )
            await asyncio.sleep(0.12)
            await page.keyboard.press("Control+A")
            await asyncio.sleep(0.05)
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.05)
            await page.keyboard.insert_text(title)
            await asyncio.sleep(0.35)
            if _normalize(await _read_title(page)) == expected:
                keyboard_ok = True
                break
        except Exception as exc:
            log.debug(f"title native commit 실패 ({sel}): {exc}")

    try:
        state = await page.evaluate(
            """(value) => {
                const selectors = [
                    ".se-title-input [contenteditable='true']",
                    ".se-title-input [contenteditable]",
                    ".se-title-text",
                    "[placeholder*='제목']"
                ];
                const norm = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
                const fire = (node, event) => { try { node.dispatchEvent(event); } catch (e) {} };
                const makeInput = (type, data = value) => {
                    try {
                        return new InputEvent(type, { bubbles: true, cancelable: true, inputType: 'insertText', data });
                    } catch (e) {
                        const ev = document.createEvent('Event');
                        ev.initEvent(type, true, true);
                        return ev;
                    }
                };
                for (const sel of selectors) {
                    const node = document.querySelector(sel);
                    if (!(node instanceof HTMLElement)) continue;
                    const wrapper = node.closest?.('.se-title-input, .se-documentTitle, .se-title') || node;
                    const target =
                        node.querySelector?.("[contenteditable='true']") ||
                        node.querySelector?.('.__se-node') ||
                        node;
                    if (!(target instanceof HTMLElement)) continue;
                    wrapper.querySelectorAll?.('.__se_placeholder, .se-placeholder')
                        ?.forEach((p) => p.remove());
                    wrapper.classList?.remove('se-is-empty');
                    target.focus();
                    fire(target, new FocusEvent('focus', { bubbles: true }));
                    fire(wrapper, new FocusEvent('focusin', { bubbles: true }));
                    fire(target, makeInput('beforeinput'));
                    target.textContent = value;
                    const range = document.createRange();
                    range.selectNodeContents(target);
                    range.collapse(false);
                    const selection = window.getSelection();
                    selection?.removeAllRanges();
                    selection?.addRange(range);
                    // IME 한글 처리를 위한 composition 이벤트 (SE3 내부 모델 갱신)
                    try {
                        fire(target, new CompositionEvent('compositionstart', { bubbles: true, cancelable: false, data: '' }));
                        fire(target, new CompositionEvent('compositionupdate', { bubbles: true, cancelable: false, data: value }));
                        fire(target, new CompositionEvent('compositionend', { bubbles: true, cancelable: false, data: value }));
                    } catch (e) {}
                    for (const el of [target, wrapper]) {
                        fire(el, makeInput('input'));
                        fire(el, new KeyboardEvent('keyup', { bubbles: true, key: 'Process' }));
                        fire(el, new Event('change', { bubbles: true }));
                    }
                    if (norm(target.innerText || target.textContent) === norm(value)) {
                        return { ok: true, selector: sel, text: target.innerText || target.textContent || '' };
                    }
                }
                return { ok: false, text: '' };
            }""",
            title,
        )
    except Exception as exc:
        log.debug(f"title JS commit 실패: {exc}")
        state = {"ok": False, "text": ""}

    await _patch_se3_title_component(page, title)
    model_ok = await _patch_document_title_model(page, title)
    actual = _normalize(await _read_title(page))
    # model_ok가 True이면 SE3 내부 모델에 제목이 주입됨 → 발행 시 제목이 전송됨
    ok = (actual == expected and (keyboard_ok or bool(state.get("ok")) or model_ok)) or model_ok
    if ok:
        log.info(f"제목 commit ok: visible='{actual[:40]}', keyboard={keyboard_ok}, js={state.get('ok')}, model={model_ok}")
    else:
        log.warning(f"제목 commit 불확실: visible='{actual[:40]}', keyboard={keyboard_ok}, js={state.get('ok')}, model={model_ok}")
    return ok


async def set_title(page: Page, title: str) -> bool:
    expected = _normalize(title)
    placeholder_strings = {"제목", "제목을 입력해 주세요"}

    # 제목 영역 활성화: 스크롤 최상단 → .se-title-input 래퍼 클릭
    await page.evaluate("() => window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)
    try:
        wrapper = page.locator(".se-title-input").first
        if await wrapper.is_visible(timeout=2000):
            await wrapper.click()
            await asyncio.sleep(0.3)
    except Exception:
        pass

    # 1단계: 클립보드 붙여넣기 후 commit
    for sel in TITLE_SELECTORS:
        try:
            el = page.locator(sel).first
            if not await el.is_visible(timeout=1500):
                continue
            await el.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            set_clipboard(title)
            await page.keyboard.press("Control+v")
            await asyncio.sleep(0.5)
            actual = _normalize(await _read_title(page))
            if actual == expected and actual not in placeholder_strings:
                await _commit_title_input_state(page, title)
                log.info(f"제목 입력 완료: {actual[:40]}")
                return True
            log.warning(f"제목 paste 검증 실패 (sel={sel}, actual='{actual[:40]}') — fallback")
        except Exception:
            continue

    # 2단계: DOM 조작 + commit fallback
    for attempt in range(3):
        for sel in TITLE_SELECTORS:
            try:
                el = page.locator(sel).first
                if not await el.is_visible(timeout=1500):
                    continue
                await el.click()
                await asyncio.sleep(0.3)
                actual = await el.evaluate(
                    """(node, value) => {
                        const wrapper = node instanceof HTMLElement ? node : null;
                        const target =
                            wrapper?.querySelector?.('.__se-node') ||
                            wrapper?.querySelector?.("[contenteditable='true']") ||
                            wrapper;
                        if (!(target instanceof HTMLElement)) return '';
                        const placeholder = wrapper?.querySelector?.('.__se_placeholder');
                        if (placeholder) placeholder.remove();
                        target.textContent = value;
                        wrapper?.classList?.remove?.('se-is-empty');
                        target.focus?.();
                        const range = document.createRange();
                        range.selectNodeContents(target);
                        range.collapse(false);
                        const selection = window.getSelection();
                        selection?.removeAllRanges();
                        selection?.addRange(range);
                        target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                        wrapper?.dispatchEvent?.(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        wrapper?.dispatchEvent?.(new Event('change', { bubbles: true }));
                        return wrapper?.innerText || target.innerText || target.textContent || '';
                    }""",
                    title,
                )
                await asyncio.sleep(0.3)
                actual = _normalize(actual or await _read_title(page))
                if actual != expected:
                    await page.keyboard.insert_text(title)
                    await asyncio.sleep(0.4)
                    actual = _normalize(await _read_title(page))
                if actual == expected:
                    await _commit_title_input_state(page, title)
                    log.info(f"제목 입력 완료 (DOM): {actual[:40]}")
                    return True
                log.warning(f"제목 DOM 시도 {attempt+1}/3 불일치: '{actual[:40]}'")
            except Exception:
                continue
        await clear_popups(page)
        await asyncio.sleep(0.6)

    try:
        await page.screenshot(path=str(LOG_DIR / "title_fail.png"))
        log.error("제목 입력 실패 — 스크린샷: logs/title_fail.png")
    except Exception:
        log.error("제목 입력 실패")
    return False


BODY_SELECTORS = [
    '.se-main-container [contenteditable]:not([contenteditable="false"])',
    '.se-component-content [contenteditable]:not([contenteditable="false"])',
    '.se-documentView_container [contenteditable]:not([contenteditable="false"])',
    '[data-type="text"][contenteditable]:not([contenteditable="false"])',
    '.se-main-container [contenteditable="true"]',
    '.se-component-content [contenteditable="true"]',
]


async def focus_body(page: Page):
    """SE3 본문 영역 포커스 (JS 직접 + Tab fallback)"""
    # 1단계: JS로 body contenteditable 직접 포커스
    focused = await page.evaluate("""(sels) => {
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
                el.focus();
                const r = document.createRange();
                r.selectNodeContents(el);
                r.collapse(false);
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(r);
                return true;
            }
        }
        return false;
    }""", BODY_SELECTORS)
    if focused:
        await asyncio.sleep(0.4)
        return

    # 2단계: JS only 재포커스 (_playwright_click_body는 흰 화면 유발 위험)
    try:
        await _refocus_body(page)
        await asyncio.sleep(0.4)
    except Exception:
        pass


async def _verify_body(page: Page) -> str:
    """본문 내용 검증 — 실제로 텍스트가 들어갔는지 확인"""
    return await page.evaluate("""(sels) => {
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
                const t = el.innerText || el.textContent || '';
                return t.trim().slice(0, 80);
            }
        }
        return '';
    }""", BODY_SELECTORS)


async def publish_post(page: Page, title: str, body: str, category: str, image_paths: list[Path], schedule_dt: datetime | None = None):
    """글쓰기 페이지 이동 → 제목/본문 입력 → 즉시/예약 발행"""
    write_url = f"https://blog.naver.com/PostWriteForm.naver?blogId={BLOG_ID}"

    # 에디터 로드 재시도 (최대 2회)
    editor_loaded = False
    for nav_attempt in range(2):
        log.info(f"글쓰기 페이지 이동: {title[:40]}")
        await page.goto(write_url, wait_until="load", timeout=90000)

        # 페이지 안정화 + 작성 중인 글 팝업 먼저 닫기 (팝업이 에디터 전체를 가림)
        await asyncio.sleep(3)
        await clear_popups(page)
        await asyncio.sleep(2)

        # 에디터 로드 확인 — blog_auto.py 검증 방식과 동일하게 제목 입력란 기준
        _EDITOR_SEL = ".se-title-text, .se-title-input [contenteditable], .se-title-input"
        try:
            await page.wait_for_selector(_EDITOR_SEL, state="visible", timeout=45000)
            editor_loaded = True
            log.info("에디터 로드 완료")
            break
        except Exception:
            current_url = page.url
            log.warning(f"에디터 미감지 (url={current_url[:60]}) — {'재시도' if nav_attempt == 0 else '포기'}")
            if nav_attempt == 0:
                await asyncio.sleep(3)

    if not editor_loaded:
        log.error("에디터 로드 실패 — 발행 중단")
        try:
            await page.screenshot(path=str(LOG_DIR / "editor_load_fail.png"))
        except Exception:
            pass
        return False

    # SE3 내부 완전 초기화 대기
    await asyncio.sleep(5)
    await clear_popups(page)
    await asyncio.sleep(2)
    await clear_popups(page)

    # 제목 입력 (DOM 조작 fallback 포함)
    title_ok = await set_title(page, title)
    if not title_ok:
        log.error("제목 입력 실패 — 발행 중단")
        return False

    # 본문 포커스 — JS only (_playwright_click_body는 흰 화면 유발 위험)
    await _refocus_body(page)
    await asyncio.sleep(0.5)

    # 본문 입력 — 평문 붙여넣기 + 이미지 업로드 (blog_auto 검증 방식)
    sections = body.split("[이미지]")
    log.info(f"본문 입력: 섹션 {len(sections)}개, 이미지 {len(image_paths)}장")
    await _clear_active_editor_inline_formatting(page)
    for i, section in enumerate(sections):
        raw = section.strip()
        if raw:
            prepared = _prepare_section_for_editor(raw)
            if prepared:
                if not await _insert_text_via_editor_model(page, prepared):
                    log.warning(f"  섹션 {i+1} SE3 모델 입력 실패 → CF_HTML 폴백")
                    await _paste_text_section_as_html(page, prepared)
                removed = await _strip_strikethrough_from_editor(page)
                if removed:
                    log.warning(f"  섹션 {i+1} 취소선 잔재 {removed}개 제거")
            log.info(f"  섹션 {i+1} 텍스트 입력")
        if i < len(image_paths):
            await upload_image(page, image_paths[i])

    await asyncio.sleep(1)

    # ── 발행 전 취소선 검사 (전체 프레임 순회) ──
    log.info("취소선 검사 중...")
    removed_before_check = await _strip_strikethrough_from_editor(page)
    if removed_before_check:
        log.warning(f"발행 전 취소선 잔재 {removed_before_check}개 제거 후 재검사")
    await page.screenshot(path=str(LOG_DIR / "pre_publish_check.png"))

    _STRIKE_JS = """() => {
        const roots = Array.from(document.querySelectorAll(
            '.se-main-container, .se-document, [class*="se-main"], [class*="editorContent"]'
        ));
        const root = roots[0] || document.body;
        if (!root) return { found: false, containerNotFound: true };

        // 실제 콘텐츠 영역만 — 툴바·버튼·플레이스홀더 제외
        const SKIP = new Set(['BUTTON','INPUT','SELECT','TEXTAREA','OPTION']);
        function isUI(el) {
            if (!(el instanceof Element)) return false;
            if (el.closest('button,[role="button"],input,select,textarea,option')) return true;
            if (el.closest('.se-toolbar,.se-floating-toolbar,.se-insert-menu,.se-help,.se-popup,.se-title-input,.se-title-text')) return true;
            if (SKIP.has(el.tagName)) return true;
            const cls = el.className || '';
            return /toolbar|button|placeholder|se-placeholder|se-help|se-title-input/i.test(cls);
        }

        // 1. strike/del/s 태그 — UI 영역 제외
        for (const el of root.querySelectorAll('s, del, strike')) {
            if (!isUI(el) && (el.textContent || '').trim())
                return { found: true, reason: 'strike 태그: "' + (el.textContent||'').trim().slice(0,50) + '"' };
        }

        // 2. inline style line-through — 직접 선언된 것만 (상속 제외)
        for (const el of root.querySelectorAll('[style*="line-through"]')) {
            if (!isUI(el) && (el.textContent || '').trim())
                return { found: true, reason: 'inline line-through: "' + (el.textContent||'').trim().slice(0,50) + '"' };
        }

        // 3. 텍스트 노드에 틸드 잔존 검사
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (!node.nodeValue) continue;
            if (isUI(node.parentElement)) continue;
            if (/~~.{1,}~~|(?<![~])~[^~\\s]+~(?![~])/.test(node.nodeValue))
                return { found: true, reason: '틸드 잔존: "' + node.nodeValue.slice(0,50) + '"' };
        }

        return { found: false, containerFound: true, bodyFallback: roots.length === 0 };
    }"""

    strike_result = {"found": False, "containerNotFound": True}
    for frame in page.frames:
        try:
            r = await frame.evaluate(_STRIKE_JS)
            if r.get("found"):
                strike_result = r
                break
            if not r.get("containerNotFound"):  # SE3 컨테이너 발견 + 취소선 없음
                strike_result = r
                break
        except Exception:
            continue

    if strike_result.get("found"):
        log.error(f"취소선 감지 — 발행 중단: {strike_result.get('reason')}")
        await page.screenshot(path=str(LOG_DIR / "strikethrough_detected.png"))
        log.error("logs/strikethrough_detected.png 확인 후 txt 파일 수정 → 재실행")
        return False
    if strike_result.get("containerNotFound"):
        log.warning("SE3 컨테이너 미발견 — 취소선 검사 불완전, 발행 계속")
    else:
        log.info("취소선 없음 — 발행 계속")

    # 발행 직전 제목 재확인 — 본문 입력 중 SE3 재렌더링으로 제목이 초기화될 수 있음
    await page.evaluate("() => window.scrollTo(0, 0)")
    await asyncio.sleep(0.6)
    preflight_title = _normalize(await _read_title(page))
    if preflight_title != _normalize(title):
        log.warning(f"발행 직전 제목 누락 감지 ('{preflight_title}') — SE3 모델 재주입 중")
        # DOM 읽기 실패해도 SE3 내부 모델에 재주입
        await _patch_se3_title_component(page, title)
        await _patch_document_title_model(page, title)
        await asyncio.sleep(0.3)
        preflight_title2 = _normalize(await _read_title(page))
        if preflight_title2 != _normalize(title):
            log.warning(f"SE3 모델 재주입 후 DOM 읽기 실패 ('{preflight_title2}') — set_title 시도")
            await set_title(page, title)
            await asyncio.sleep(0.5)
        log.info(f"제목 재주입 결과: '{_normalize(await _read_title(page))}'")
    else:
        log.info(f"발행 직전 제목 확인 OK: '{preflight_title[:40]}'")

    # 발행 팝업 열기 (라이브러리 패널 등 닫기)
    log.info("발행 팝업 열기...")
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    except Exception:
        pass
    await clear_popups(page)
    opened = False
    for sel in ["button[class*='publish_btn__']", "button:has-text('발행')", "button[class*='publish']"]:
        try:
            btn = page.locator(sel).last
            if await btn.is_visible(timeout=3000):
                await btn.click()
                opened = True
                log.info(f"발행 버튼 클릭: {sel}")
                break
        except Exception:
            continue
    if not opened:
        log.error("발행 팝업 버튼을 찾을 수 없음")
        return False
    await asyncio.sleep(3)

    await page.screenshot(path=str(LOG_DIR / "publish_popup.png"))

    # 카테고리 설정
    if category:
        log.info(f"카테고리 설정: {category}")
        try:
            cat_btn = page.locator("[class*='option_category__']").first
            if await cat_btn.is_visible(timeout=2000):
                await cat_btn.click()
                await asyncio.sleep(1)
                cat_item = page.locator(f"li:has-text('{category}')").first
                if await cat_item.is_visible(timeout=2000):
                    await cat_item.click()
                    log.info(f"카테고리 선택: {category}")
                    await asyncio.sleep(0.5)
        except Exception as e:
            log.warning(f"카테고리 설정 실패 (계속 진행): {e}")

    # 예약 발행 설정
    if schedule_dt:
        try:
            log.info(f"예약 발행 설정: {schedule_dt.strftime('%Y-%m-%d %H:%M')}")
            await page.click("label[for='radio_time2']")
            await asyncio.sleep(1)

            await page.click("input[class*='input_date__']")
            await asyncio.sleep(0.5)
            today = datetime.now()
            months_diff = (schedule_dt.year - today.year) * 12 + (schedule_dt.month - today.month)
            for _ in range(months_diff):
                await page.click("button.ui-datepicker-next")
                await asyncio.sleep(0.5)

            day_str = str(schedule_dt.day)
            await page.evaluate(f"""() => {{
                const btns = document.querySelectorAll('.ui-datepicker button.ui-state-default');
                const target = Array.from(btns).find(
                    b => b.textContent.trim() === '{day_str}' &&
                    !b.closest('td')?.classList.contains('ui-datepicker-other-month')
                );
                if (target) target.click();
            }}""")
            await asyncio.sleep(1)

            hour_val = f"{schedule_dt.hour:02d}"
            await page.evaluate(f"""() => {{
                const h = document.querySelector('[class*=hour_option__]');
                const m = document.querySelector('[class*=minute_option__]');
                if (h) {{ h.value = '{hour_val}'; h.dispatchEvent(new Event('change', {{bubbles: true}})); }}
                if (m) {{ m.selectedIndex = 0; m.dispatchEvent(new Event('change', {{bubbles: true}})); }}
            }}""")
            await asyncio.sleep(0.5)

            await page.click("button[class*='confirm_btn__']")
            await asyncio.sleep(3)
            log.info(f"예약 완료: [{schedule_dt.strftime('%Y-%m-%d %H:%M')}] {title}")
            if "PostView.naver" in page.url:
                log.error(f"즉시발행 감지 (예약 설정 실패): {page.url}")
                return False
            return True
        except Exception as e:
            log.warning(f"예약 발행 설정 실패, 즉시 발행으로 전환: {e}")

    # SE3 팝업 제거 후 발행 확인 — .__se-sentry 제외 (툴바 버튼 클래스와 중복)
    await page.evaluate("""
        document.querySelectorAll('.se-popup-dim,.se-popup-alert,.se-popup-alert-confirm')
                .forEach(el => el.remove())
    """)
    await asyncio.sleep(0.5)

    # 팝업 열리면서 SE3가 DOM 재렌더링으로 제목을 초기화할 수 있음 — 확인 클릭 직전 재주입
    try:
        title_reinjected = await page.evaluate("""
            (title) => {
                const fire = (node, ev) => { try { node.dispatchEvent(ev); } catch(e) {} };
                const sels = [
                    '.se-title-input [contenteditable="true"]',
                    '.se-title-input [contenteditable]',
                    '.se-title-text'
                ];
                for (const sel of sels) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const wrapper = el.closest('.se-title-input, .se-documentTitle') || el.parentElement;
                    const ph = wrapper?.querySelector('.__se_placeholder, .se-placeholder');
                    if (ph) ph.remove();
                    wrapper?.classList?.remove('se-is-empty');
                    el.focus();
                    el.textContent = title;
                    fire(el, new CompositionEvent('compositionstart', {bubbles:true, cancelable:false, data:''}));
                    fire(el, new CompositionEvent('compositionupdate', {bubbles:true, cancelable:false, data:title}));
                    fire(el, new CompositionEvent('compositionend', {bubbles:true, cancelable:false, data:title}));
                    fire(el, new InputEvent('input', {bubbles:true, cancelable:true, inputType:'insertText', data:title}));
                    fire(el, new Event('change', {bubbles:true}));
                    return (el.innerText || el.textContent || '').trim();
                }
                return '';
            }
        """, title)
        log.info(f"확인 직전 제목 재주입: '{str(title_reinjected)[:40]}'")
        await asyncio.sleep(0.3)
    except Exception as e:
        log.warning(f"확인 직전 제목 재주입 실패: {e}")

    # SE3 내부 모델 최종 동기화 (setDocumentData)
    await _patch_se3_title_component(page, title)
    await asyncio.sleep(0.2)

    log.info("발행 확인 버튼 클릭...")
    clicked = False

    # 좌표 기반 클릭 (가장 신뢰도 높음)
    try:
        point = await page.evaluate("""
            () => {
                const isVisible = el => {
                    if (!(el instanceof HTMLElement) || !el.isConnected) return false;
                    const s = window.getComputedStyle(el);
                    if (s.display==='none'||s.visibility==='hidden'||s.pointerEvents==='none') return false;
                    const r = el.getBoundingClientRect();
                    return r.width>0 && r.height>0 && r.bottom>0 && r.top<window.innerHeight;
                };
                const btns = Array.from(document.querySelectorAll('button,[role="button"]'))
                    .filter(isVisible)
                    .map(el => {
                        const r = el.getBoundingClientRect();
                        return {
                            text: (el.innerText||el.textContent||'').replace(/\\s+/g,' ').trim(),
                            cls: String(el.className||''),
                            x: r.left+r.width/2, y: r.top+r.height/2,
                            top: r.top, right: r.right
                        };
                    });
                const candidates = btns.filter(b =>
                    b.top>100 && (
                        /발행|완료|확인/.test(b.text) ||
                        /confirm_btn__|publish_btn__|submit_btn__|ok_btn__/i.test(b.cls)
                    )
                );
                candidates.sort((a,b)=>(b.top-a.top)||(b.right-a.right));
                return candidates[0]||null;
            }
        """)
        if point:
            await page.mouse.click(point["x"], point["y"])
            log.info(f"발행 확인 좌표 클릭: {point}")
            clicked = True
    except Exception as e:
        log.warning(f"좌표 클릭 실패: {e}")

    if not clicked:
        for sel in [
            "button[class*='confirm_btn__']",
            "button[class*='submit_btn__']",
            "button[class*='publish_btn__']",
            "button[class*='submit']",
            "button[class*='ok']",
            "button:has-text('완료')",
            "button:has-text('확인')",
        ]:
            try:
                btn = page.locator(sel).last
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    clicked = True
                    log.info(f"발행 확인 선택자 클릭: {sel}")
                    break
            except Exception:
                continue

    if not clicked:
        log.error("발행 확인 버튼을 찾을 수 없음")
        return False

    # 발행 확인 직후 스크린샷 + 알림창 처리
    await asyncio.sleep(1.0)
    await page.screenshot(path=str(LOG_DIR / "post_confirm.png"))
    log.info("발행 확인 직후 스크린샷: logs/post_confirm.png")

    # "내용을 입력해주세요" 같은 alert/dialog 처리
    for _ in range(3):
        try:
            alert_closed = await page.evaluate("""() => {
                const selectors = [
                    '.se-popup-alert button',
                    '.se-popup-alert-confirm button',
                    '[class*="alert"] button',
                    '[class*="dialog"] button',
                    '[class*="modal"] button[class*="ok"]',
                    '[class*="modal"] button[class*="confirm"]',
                ];
                for (const sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        return sel;
                    }
                }
                return null;
            }""")
            if alert_closed:
                log.warning(f"발행 후 알림창 닫음: {alert_closed}")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    # 발행 완료 확인 — URL이 PostWriteForm에서 벗어나야 진짜 발행 성공
    success = False
    for _ in range(40):  # 최대 20초 대기
        await asyncio.sleep(0.5)
        current_url = page.url
        if "PostWriteForm" not in current_url and "blog.naver.com" in current_url:
            success = True
            log.info(f"발행 URL 확인: {current_url}")
            break
    await page.screenshot(path=str(LOG_DIR / "published.png"))
    if success:
        log.info(f"발행 완료: {title[:40]}")
        return True
    else:
        log.error(f"발행 실패 (URL 미변경, 현재: {page.url}): {title[:40]}")
        return False


# ── 뮤텍스 락 ──

LOCK_FILE = Path("C:/Users/l/scripts/.naver_publish.lock")

class PublishLock:
    """blog_auto와 econ_publisher 동시 실행 방지용 파일 락"""
    def __init__(self):
        self._acquired = False

    def __enter__(self):
        for _ in range(30):  # 최대 150초 대기
            try:
                if LOCK_FILE.exists():
                    pid = LOCK_FILE.read_text().strip()
                    import psutil  # type: ignore
                    try:
                        if psutil.pid_exists(int(pid)):
                            import time; time.sleep(5)
                            continue
                    except Exception:
                        pass
                LOCK_FILE.write_text(str(os.getpid()))
                self._acquired = True
                return self
            except Exception:
                import time; time.sleep(5)
        log.warning("락 획득 실패 — 강제 진행")
        return self

    def __exit__(self, *_):
        if self._acquired:
            try:
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass


# ── 메인 ──

async def main():
    txts = sorted(PENDING_DIR.glob("*.txt"))
    if not txts:
        log.info("발행할 글이 없습니다.")
        return

    log.info(f"발행 대상: {len(txts)}편")

    lock = PublishLock()
    lock.__enter__()
    try:
        total = len(txts)
        pending_items: list = []
        for idx, txt in enumerate(txts):
            result = parse_post(txt)
            if not result:
                log.warning(f"파싱 실패, 스킵: {txt.name}")
                continue
            title, category, img_keyword, body = result
            if not title:
                log.warning(f"제목 없음, 스킵: {txt.name}")
                continue

            # 이미지 없으면 card_generator로 자동 생성
            image_paths = get_image_paths(txt.name)
            if not image_paths and "[이미지]" in body:
                try:
                    from card_generator import generate_cards
                    stem = txt.stem
                    parts = stem.split("_")
                    code = (parts[0] + parts[1]) if len(parts) >= 2 and parts[1].isdigit() else stem[:8]
                    out_dir = IMAGES_DIR / code
                    log.info(f"카드 이미지 자동 생성: {out_dir}")
                    generate_cards(txt, out_dir)
                    image_paths = get_image_paths(txt.name)
                    log.info(f"카드 생성 완료: {len(image_paths)}장")
                except Exception as e:
                    log.warning(f"카드 생성 실패 (계속 진행): {e}")

            pending_items.append((txt, title, body, category, image_paths))

        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
            page = browser.pages[0] if browser.pages else await browser.new_page()
            try:
                await login(page)
                for idx, (txt, title, body, category, image_paths) in enumerate(pending_items):
                    log.info(f"--- [{idx + 1}/{total}] {txt.name} | 이미지 {len(image_paths)}장 ---")
                    try:
                        ok = await publish_post(page, title, body, category, image_paths, schedule_dt=parse_schedule(txt.name))
                        if ok:
                            move_to_done(txt)
                        else:
                            log.error(f"발행 실패: {txt.name}")
                    except Exception as e:
                        log.error(f"발행 중 오류 ({txt.name}): {e}")
                    if idx < len(pending_items) - 1:
                        await asyncio.sleep(3)
            finally:
                try:
                    await asyncio.sleep(2)
                    await browser.close()
                except Exception:
                    pass

        log.info("모든 발행 완료")
    finally:
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
