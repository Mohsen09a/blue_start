@echo off
setlocal
for %%I in ("%~dp0..\..\..") do set "REPO_ROOT=%%~fI"
if defined PYTHON_EXE (set "PYTHON_CMD=%PYTHON_EXE%") else set "PYTHON_CMD=python"
cd /d "%REPO_ROOT%"
set "PYTHONPATH=%REPO_ROOT%\src;%REPO_ROOT%"
"%PYTHON_CMD%" -m pytest new_research\starterpack_prediction_time_split\tests -q
endlocal
