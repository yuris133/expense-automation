@echo off
rem Запуск Telegram-бота приёма чеков.
rem Планировщик заданий Windows вызывает этот файл (задача "ReceiptsBot", создаётся scripts\setup_autostart.bat).
cd /d "%~dp0.."
if not exist logs mkdir logs
echo [%date% %time%] run_bot.bat started >> logs\bot_console.log

rem цикл автоперезапуска: если бот упал — поднимаем через 30 секунд
:loop
python src\telegram_bot.py >> logs\bot_console.log 2>&1
echo [%date% %time%] python exited with %errorlevel%, restart in 30 s >> logs\bot_console.log
timeout /t 30 /nobreak >> logs\bot_console.log 2>&1
goto :loop
