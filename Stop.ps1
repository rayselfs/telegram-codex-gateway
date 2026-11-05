$ErrorActionPreference = 'Stop'
Set-Content -LiteralPath 'D:\CodexTelegram\profile\stop.request' -Value 'stop'
Write-Output 'Graceful stop requested for this gateway only.'
