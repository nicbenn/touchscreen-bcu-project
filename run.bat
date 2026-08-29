@echo off
cd /d "%~dp0"
:loop
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" run.py
) else (
  "%LocalAppData%\Programs\Python\Python312\python.exe" run.py
)
if exist "%~dp0data\.bcu-restart" (
  del "%~dp0data\.bcu-restart"
  goto loop
)
