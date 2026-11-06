param([Parameter(Mandatory=$true)][string]$Config,[long]$Owner)
$ErrorActionPreference='Stop'
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$arguments=@('-B',(Join-Path $cfg.code_root 'background.py'),'--config',$Config,'--install')
if($Owner){$arguments+=@('--owner',[string]$Owner)}
& $cfg.python @arguments
if($LASTEXITCODE -ne 0){throw 'Background setup failed; inspect existing task bindings'}
