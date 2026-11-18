@echo off
rem Создаёт задачу Планировщика "ReceiptsBot": бот запускается при входе в Windows.
rem Запуск: двойной клик (нужны права администратора) — Windows сам запросит их при старте.
rem Удалить задачу вручную: schtasks /Delete /TN "ReceiptsBot" /F

net session >nul 2>&1
if errorlevel 1 (
  echo Нужны права администратора — перезапускаю скрипт с запросом UAC...
  powershell -NoProfile -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0.."
schtasks /Delete /TN "ReceiptsBot" /F >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_autostart.ps1"
schtasks /Query /TN "ReceiptsBot" /FO LIST >> logs\setup_task.log 2>&1