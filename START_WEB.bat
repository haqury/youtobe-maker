@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo YouTube Panel (Streamlit)
echo Откроется браузер на http://localhost:8501
echo.
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
streamlit run studio\app.py --server.headless true
echo.
pause