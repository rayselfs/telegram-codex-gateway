param([Parameter(Mandatory=$true)][string]$Config,[long]$Owner)
$ErrorActionPreference='Stop'
$Config=[IO.Path]::GetFullPath($Config)
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$arguments=@('-B','-m','gateway','background','--config',$Config,'--install')
if($Owner){$arguments+=@('--owner',[string]$Owner)}
Push-Location -LiteralPath $cfg.code_root
try { & $cfg.python @arguments } finally { Pop-Location }
if($LASTEXITCODE -ne 0){throw 'Background setup failed; inspect existing task bindings'}
