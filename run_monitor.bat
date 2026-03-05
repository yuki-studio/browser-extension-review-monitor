@echo off
cd /d "C:\Users\fab\browser-review-monitor"
python monitor.py run 1>> "C:\Users\fab\browser-review-monitor\logs\monitor.out.log" 2>> "C:\Users\fab\browser-review-monitor\logs\monitor.err.log"
