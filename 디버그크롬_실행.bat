@echo off
chcp 65001 >nul
echo Starting Chrome in debug mode (port 9222)...
echo.

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\chrome-debug-jangma" https://nid.naver.com/nidlogin.login

if errorlevel 1 (
    echo Chrome not found at default path. Trying x86...
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\chrome-debug-jangma" https://nid.naver.com/nidlogin.login
)

echo.
echo === NEXT STEPS ===
echo 1. Login with new Naver ID in the Chrome window
echo 2. Visit your new blog (jangma_blog) once
echo 3. Open another cmd and run:
echo    py "C:\Users\l\scripts\econ_auto\upload_skin.py"
echo.
pause
