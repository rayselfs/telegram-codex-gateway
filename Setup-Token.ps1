$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$secret = Read-Host 'Paste the NEW BotFather token (hidden)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ($plain -notmatch '^\d+:[A-Za-z0-9_-]{20,}$') { throw 'Invalid token format' }
    $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $protected = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes('D:\CodexTelegram\profile\telegram-token.dpapi', $protected)
    Write-Output 'Token saved encrypted for this Windows user. No message was sent.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
    $plain = $null
    $secret.Dispose()
}
