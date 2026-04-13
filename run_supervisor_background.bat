@echo off
set "WD=%~dp0"
cd /d "%WD%"
start "" powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%WD%service_supervisor.ps1"
