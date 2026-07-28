@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHON_EXE=C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe"
"%PYTHON_EXE%" -m pip install -e ".[analysis]"
