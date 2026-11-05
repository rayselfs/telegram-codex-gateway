$ErrorActionPreference = 'Stop'
if (-not (Test-Path 'D:\CodexTelegram\profile\telegram-token.dpapi')) { throw 'Run Setup-Token.ps1 first' }
if (Test-Path 'D:\CodexTelegram\profile\maintenance.request') { throw 'Maintenance hold is active' }
Remove-Item -LiteralPath 'D:\CodexTelegram\profile\stop.request' -ErrorAction SilentlyContinue
Start-Process -WindowStyle Hidden -FilePath 'D:\CodexTelegram\runtime\gateway-venv\Scripts\python.exe' -ArgumentList @('-B','D:\CodexTelegram\bridge\gateway_guard.py') -WorkingDirectory 'D:\CodexTelegram\bridge' -RedirectStandardOutput 'D:\CodexTelegram\profile\stdout.log' -RedirectStandardError 'D:\CodexTelegram\profile\stderr.log'
Write-Output 'Start requested. Inspect profile\health.json; this is not confirmation of readiness.'
