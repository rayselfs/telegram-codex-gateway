param([Parameter(Mandatory=$true)][string]$Config,[Parameter(Mandatory=$true)][long]$Owner)
$ErrorActionPreference='Stop'
$Config=[IO.Path]::GetFullPath($Config)
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
Push-Location -LiteralPath $cfg.code_root
try { & $cfg.python -B -m gateway worker --config $Config --owner $Owner } finally { Pop-Location }
exit $LASTEXITCODE
