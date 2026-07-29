@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONPATH=%CD%\src"
set "MPLBACKEND=Agg"
set "PYTHON_EXE=C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe"
"%PYTHON_EXE%" -m blue_start.cli doctor || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli prepare || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli nodes || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli starterpacks || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli starterpack-components || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli following --time-std --impossible-timestamps || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli kendall-tau --follow-profile full --top-k 1000000 || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli reference-import || exit /b 1
call scripts\run_remaining_paper_tasks.cmd || exit /b 1
"%PYTHON_EXE%" -m blue_start.cli plot all --follow-profile full || exit /b 1
echo Full workstation-safe pipeline completed successfully.
