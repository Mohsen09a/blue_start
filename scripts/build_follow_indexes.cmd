@echo off
setlocal
cd /d "%~dp0\.."
C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe scripts\build_follow_indexes.py %*
