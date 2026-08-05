@echo off
setlocal
cd /d "%~dp0\..\..\.."
"C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" new_research\starterpack_growth_effect_full_population\scripts\prepare_isolated_database.py %*
endlocal
