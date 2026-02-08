@echo off
chcp 65001 >nul
echo Останавливаю MyPCBot...
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq *bot.py*" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *bot.py*" 2>nul

for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq pythonw.exe" ^| find /c "pythonw"') do set count=%%a
echo Готово!
pause
