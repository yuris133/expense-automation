# Создаёт задачу Планировщика "ReceiptsBot": бот приёма чеков запускается при входе в Windows.
# Запуск: в PowerShell  powershell -File scripts\setup_autostart.ps1   (от администратора, если schtasks откажет).
# Удалить задачу: Unregister-ScheduledTask -TaskName "ReceiptsBot" -Confirm:$false

Set-Location -Path (Join-Path $PSScriptRoot "..")
$out = "logs\setup_task.log"
"=== setup_autostart.ps1: $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')" | Out-File $out -Encoding utf8

$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument ('"' + "$PSScriptRoot\run_bot_hidden.vbs" + '"') `
    -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive

try {
    Register-ScheduledTask -TaskName "ReceiptsBot" -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    "УСПЕХ: задача ReceiptsBot создана/обновлена" | Out-File $out -Append -Encoding utf8
} catch {
    "ОШИБКА: $($_.Exception.Message)" | Out-File $out -Append -Encoding utf8
}