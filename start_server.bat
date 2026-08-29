@echo off
cd /d c:\proyectos\SPACE LAIR
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
start /b cmd /c "set PYTHONIOENCODING=utf-8 && chcp 65001 >nul && python run.py web > server_e2e_out.log 2>&1"
echo Server starting...
