# coding: utf-8
"""
장마감후여기 전체 자동화 파이프라인
1. 시장 데이터 수집 (market_collector)
2. 카드 이미지 생성 (card_generator — subprocess)
3. 블로그 포스트 텍스트 생성 (post_generator — Groq API)
4. 네이버 블로그 발행 (econ_publisher)

실행: py econ_auto.py [YYMMDD]
"""

import sys
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))


def step1_collect(date_yymmdd: str) -> dict:
    print(f"\n[1/4] 시장 데이터 수집 ({date_yymmdd})")
    from collectors.market_collector import collect_all
    data = collect_all(date_yymmdd)
    kospi = data.get("kospi", {})
    print(f"  코스피: {kospi.get('close', '?'):,} ({kospi.get('change_pct', '?'):+.2f}%)")
    return data


def step2_cards(date_yymmdd: str):
    print(f"\n[2/4] 카드 이미지 생성")
    card_script = BASE_DIR / "generators" / "card_generator.py"
    result = subprocess.run(
        [sys.executable, str(card_script), date_yymmdd],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        print(f"  [경고] 카드 생성 오류:\n{result.stderr}")
    else:
        print(f"  카드 이미지 생성 완료: posts/images/{date_yymmdd}/")
    if result.stdout:
        print(result.stdout)


def step3_generate_post(data: dict, date_yymmdd: str) -> Path:
    print(f"\n[3/4] 포스트 텍스트 생성 (Groq API)")
    from generators.post_generator import generate
    post_path = generate(data, date_yymmdd)
    return post_path


def step4_publish():
    print(f"\n[4/4] 네이버 블로그 발행")
    import econ_publisher
    asyncio.run(econ_publisher.main())


def main():
    # 날짜 결정
    if len(sys.argv) > 1:
        date_yymmdd = sys.argv[1]
    else:
        date_yymmdd = datetime.now().strftime("%y%m%d")

    print(f"=== 장마감후여기 자동화 파이프라인 시작 ({date_yymmdd}) ===")

    try:
        data = step1_collect(date_yymmdd)
    except Exception as e:
        print(f"[오류] 데이터 수집 실패: {e}")
        sys.exit(1)

    try:
        step2_cards(date_yymmdd)
    except Exception as e:
        print(f"[경고] 카드 생성 실패 (계속 진행): {e}")

    try:
        step3_generate_post(data, date_yymmdd)
    except Exception as e:
        print(f"[오류] 포스트 생성 실패: {e}")
        sys.exit(1)

    try:
        step4_publish()
    except Exception as e:
        print(f"[오류] 발행 실패: {e}")
        sys.exit(1)

    print(f"\n=== 완료 ===")


if __name__ == "__main__":
    main()
