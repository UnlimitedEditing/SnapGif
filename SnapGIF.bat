@echo off
title SnapGIF

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.9+ from https://python.org
    pause
    exit /b 1
)

:: Install / upgrade dependencies silently
echo Checking dependencies...
pip install --quiet --upgrade Pillow mss

:: Launch SnapGIF (hidden console window)
echo Starting SnapGIF...
start "" pythonw "%~dp0snapgif.py"
