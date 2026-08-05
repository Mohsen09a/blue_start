@echo off
setlocal
cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%\src;%CD%"
"C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" new_research\starterpack_growth_effect\scripts\render_report_figures.py
endlocal
