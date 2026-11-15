@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start Workbench.ps1" -Demo
if errorlevel 1 pause
