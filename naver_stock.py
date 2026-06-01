"""네이버 금융 크롤링으로 주식 데이터 수집 (pykrx KRX API 대체)"""
from __future__ import annotations
import re
import logging
import requests
from datetime import datetime

log = logging.getLogger(__name__)

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def collect_stock_data(ticker: str, name: str) -> dict | None:
    try:
        from bs4 import BeautifulSoup as BS
    except ImportError:
        BS = None

    try:
        # ── 현재가 / 등락률 ──────────────────────────────────
        r1 = requests.get(
            f'https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker}',
            headers=_HEADERS, timeout=10)
        d = r1.json()['datas'][0]
        close   = int(d['closePrice'].replace(',', ''))
        chg_pct = float(d['fluctuationsRatio'])
        if d['compareToPreviousPrice']['code'] in ('5', '4'):
            chg_pct = -abs(chg_pct)

        # ── 시총 / 52주 / PER / PBR ─────────────────────────
        high_52w = low_52w = market_cap = 0
        per = pbr = 0.0
        # 수급 데이터 (최근 20일 기준, 기관/외국인 순매매대금 추산)
        foreign_net = inst_net = retail_net = 0
        try:
            r3 = requests.get(f'https://finance.naver.com/item/frgn.naver?code={ticker}&page=1', headers=_HEADERS, timeout=10)
            if BS:
                soup3 = BS(r3.content, 'html.parser')
                tables = soup3.find_all('table', {'class': 'type2'})
                if len(tables) > 1:
                    trs = tables[1].find_all('tr', {'onmouseover': 'mouseOver(this)'})
                    f_sum = i_sum = 0
                    for tr in trs:
                        tds = tr.find_all('td')
                        if len(tds) >= 7:
                            try:
                                close_p = int(tds[1].text.strip().replace(',', ''))
                                i_vol = int(tds[5].text.strip().replace(',', ''))
                                f_vol = int(tds[6].text.strip().replace(',', ''))
                                i_sum += i_vol * close_p
                                f_sum += f_vol * close_p
                            except:
                                pass
                    foreign_net = f_sum // 100000000
                    inst_net = i_sum // 100000000
        except Exception as e:
            log.warning(f'수급 파싱 실패: {e}')
        try:
            r2 = requests.get(
                f'https://finance.naver.com/item/main.naver?code={ticker}',
                headers=_HEADERS, timeout=10)
            if BS:
                soup = BS(r2.content, 'html.parser')
                tab  = soup.find('div', id='tab_con1')
                txt  = tab.get_text() if tab else ''
            else:
                txt = r2.content.decode('utf-8', errors='replace')

            # 시총: "67조 1,901 억원"
            m = re.search(r'(\d+)\s*조\s+([\d,]+)\s+억원', txt)
            if m:
                market_cap = int(m.group(1)) * 10000 + int(m.group(2).replace(',', ''))

            # 52주 최고/최저 (형식 두 가지 대응)
            mh = re.search(r'52주 최고 : ([\d,]+)', txt)
            ml = re.search(r'52주 최저 : ([\d,]+)', txt)
            if mh and ml:
                high_52w = int(mh.group(1).replace(',', ''))
                low_52w  = int(ml.group(1).replace(',', ''))
            else:
                m52 = re.search(r'(?s)52주.{0,60}?([\d,]{5,})(?:\D{0,15}?)([\d,]{5,})', txt)
                if m52:
                    high_52w = int(m52.group(1).replace(',', ''))
                    low_52w  = int(m52.group(2).replace(',', ''))

            # PER (N/A인 경우 추정PER 사용)
            per_sec = re.search(r'(?s)PER/EPS(.*?)추정PER', txt)
            if per_sec:
                mp = re.search(r'(\d+\.\d+)배', per_sec.group(1))
                if mp:
                    per = float(mp.group(1))
                else:
                    mp2 = re.search(r'(?s)추정PERlEPS.{0,300}?(\d+\.\d+)배', txt)
                    if mp2: per = float(mp2.group(1))

            # PBR
            mb = re.search(r'(?s)PBRlBPS.{0,500}?(\d+\.\d+)배', txt)
            if mb: pbr = float(mb.group(1))

        except Exception as e:
            log.warning(f'네이버 파싱 실패: {e}')

        # 핵심 데이터 없으면 스킵
        if market_cap == 0 and per == 0.0:
            log.error(f'{name}: 시총/PER 모두 0 — 글 생성 스킵')
            return None

        return {
            'ticker':      ticker,
            'name':        name,
            'close':       close,
            'chg_pct':     chg_pct,
            'high_52w':    high_52w,
            'low_52w':     low_52w,
            'market_cap':  market_cap,
            'per':         per,
            'pbr':         pbr,
            'foreign_net': foreign_net,
            'inst_net':    inst_net,
            'retail_net':  retail_net,
            'date_str':    datetime.now().strftime('%Y년 %m월 %d일'),
            'date_short':  datetime.now().strftime('%y/%m/%d'),
        }
    except Exception as e:
        log.error(f'데이터 수집 실패 ({name}): {e}')
        return None
