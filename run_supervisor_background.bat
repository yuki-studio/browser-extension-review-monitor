@echo off
cd /d C:\Users\fab\browser-review-monitor
start "" powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\fab\browser-review-monitor\service_supervisor.ps1"
