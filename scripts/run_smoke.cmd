@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONPATH=%CD%\src"
set "PYTHON_EXE=C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe"
"%PYTHON_EXE%" -m blue_start.cli doctor || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli prepare || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli nodes || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli starterpacks || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli following --row-limit 1000000 --time-std --impossible-timestamps || exit /b 1
echo Smoke pipeline completed successfully.
