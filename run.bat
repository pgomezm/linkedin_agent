@echo off
cd /d %~dp0
call venv\Scripts\activate
python linkedin_agent.py
pause
