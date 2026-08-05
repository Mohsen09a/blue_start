@echo off
setlocal
cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%\src;%CD%"
"C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m blue_start.cli starterpack-growth-study %*
endlocal
