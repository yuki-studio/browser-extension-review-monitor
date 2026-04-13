@echo off
set "WD=%~dp0"
cd /d "%WD%"
python monitor.py run 1>> "%WD%logs\monitor.out.log" 2>> "%WD%logs\monitor.err.log"
