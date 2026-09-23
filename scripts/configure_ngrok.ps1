. "$PSScriptRoot/public_demo_common.ps1"
if (-not (Test-Path -LiteralPath $demoNgrok)) { throw 'ngrok is missing from .cache/ngrok/ngrok.exe.' }
Write-Host 'Sign in at https://dashboard.ngrok.com/get-started/your-authtoken'
Write-Host 'Paste your authtoken below. Input is hidden and is not written to shell history.'
$demoSecureToken = Read-Host 'ngrok authtoken' -AsSecureString
$demoTokenPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($demoSecureToken)
try {
    $demoPlainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($demoTokenPointer)
    if ([string]::IsNullOrWhiteSpace($demoPlainToken)) { throw 'No token entered.' }
    & $demoNgrok config add-authtoken $demoPlainToken
    if ($LASTEXITCODE -ne 0) { throw 'ngrok could not save the account configuration.' }
} finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($demoTokenPointer)
    $demoPlainToken = $null
    $demoSecureToken.Dispose()
}
Write-Host 'Account configured. Start the public demo with scripts/start_public_demo.ps1.'
