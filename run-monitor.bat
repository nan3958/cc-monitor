@echo off
set PYTHONIOENCODING=utf-8
set CC_MONITOR_NOTIFY=C:\Users\Nan\.local\cc-monitor\notify.bat
pythonw -X utf8 "C:\Users\Nan\.local\cc-monitor\cc-monitor.py" >> "C:\Users\Nan\.local\cc-monitor\cc-monitor.log" 2>&1
