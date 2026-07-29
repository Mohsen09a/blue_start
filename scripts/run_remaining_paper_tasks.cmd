@echo off
setlocal
cd /d "%~dp0\.."

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli follow-wcc
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli follow-scc
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli starterpack-kcore
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli starterpack-leiden --import-native
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli edge-entropy --label-source independent
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli configuration-model --label-source independent --swaps-per-edge 10 --seed 0
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli pair-cooccurrence-paper --keep-pair-rows
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli clique-projection
if errorlevel 1 exit /b %errorlevel%

C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe -m blue_start.cli s-line-full
if errorlevel 1 exit /b %errorlevel%
