' Скрытый запуск Telegram-бота чеков для Планировщика задач.
' Планировщик вызывает этот VBS (wscript.exe): окно консоли никогда не появляется,
' случайно закрыть бот невозможно; падения переживаются петлёй автоперезапуска в run_bot.bat.
' Логи как раньше: logs\bot_console.log и logs\telegram_bot.log.
Option Explicit
Dim sh, fso, root
Set sh = WScript.CreateObject("WScript.Shell")
Set fso = WScript.CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.Run "cmd.exe /c """ & root & "\scripts\run_bot.bat""", 0, False