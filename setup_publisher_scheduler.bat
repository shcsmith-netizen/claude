@echo off
chcp 65001 >nul
echo [장마여] 자동화 스케줄러 등록 중...

REM 기존 작업 삭제
schtasks /delete /tn "장마감후여기_자동발행" /f >nul 2>&1
schtasks /delete /tn "장마감후여기_전체자동화" /f >nul 2>&1

REM 평일 16:10 전체 파이프라인 실행 (수집→카드→글생성→발행)
schtasks /create ^
  /tn "장마감후여기_전체자동화" ^
  /tr "\"C:\Users\l\scripts\econ_auto\시황자동화.bat\"" ^
  /sc weekly ^
  /d MON,TUE,WED,THU,FRI ^
  /st 16:10 ^
  /ru "%USERNAME%" ^
  /f

if %errorlevel% == 0 (
    echo.
    echo 등록 완료: 평일 오후 4시 10분 자동 실행
    echo 작업명: 장마감후여기_전체자동화
    echo 파이프라인: 데이터수집 ^> 카드생성 ^> Groq글생성 ^> 네이버발행
) else (
    echo.
    echo 등록 실패. 관리자 권한으로 다시 실행해 보세요.
)
pause
