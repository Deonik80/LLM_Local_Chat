@echo off
cd /d "%~dp0" 
lms server start
python.exe -m pip install -r requirements.txt
python.exe app.py

