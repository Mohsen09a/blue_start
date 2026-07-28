@echo off
setlocal
cd /d "%~dp0\.."
if exist "reference\upstream-a-blue-start\.git" (
  echo Upstream repository already exists.
  exit /b 0
)
git clone --depth 1 https://github.com/nwlandry/a-blue-start.git "reference\upstream-a-blue-start"

