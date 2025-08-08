@echo off
title Real Debrid Toolbox

:menu
cls
echo ======================================
echo 1. Upload Torrents (in current dir)
echo 2. DeDupe Torrents (already uploaded)
echo 3. Status Check (currently downloading)
echo ======================================
set /p choice=Enter your choice (1-3): 

if "%choice%"=="1" (
    python upload.py
) else if "%choice%"=="2" (
    python dedupe.py
) else if "%choice%"=="3" (
    python statuscheck.py
) else (
    echo Invalid choice. Please enter 1, 2, or 3.
    pause
    goto menu
)

pause
