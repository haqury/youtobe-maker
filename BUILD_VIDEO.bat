@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo Сборка эпизода: озвучка, картинки и ролик канала сна.
echo Можно вставить путь к папке или только ID, например SH01.
echo.

set "EPISODE="
set /p EPISODE=Путь к папке эпизода или ID: 
if "%EPISODE%"=="" (
    echo Путь не указан.
    echo.
    pause
    exit /b 1
)

python scripts\make_episode.py "%EPISODE%"
echo.
pause
