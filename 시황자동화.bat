@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d C:\Users\l\scripts\econ_auto
echo [장마여] 전체 자동화 파이프라인 시작...
echo 수집 ^> 카드생성 ^> 포스트생성 ^> 발행
echo.
py econ_auto.py %1
echo.
pause
