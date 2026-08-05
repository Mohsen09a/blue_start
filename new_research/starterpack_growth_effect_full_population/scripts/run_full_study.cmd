@echo off
setlocal
cd /d "%~dp0\..\..\.."
set "PYTHONPATH=%CD%\src;%CD%"
"C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" new_research\starterpack_growth_effect_full_population\scripts\run_full_study.py
endlocal
