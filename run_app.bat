@echo off
cd /d "%~dp0" 
lms server start
python.exe app.py
