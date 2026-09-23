. "$PSScriptRoot/public_demo_common.ps1"
$entries = @(Get-DemoProcesses)
# Disconnect the public endpoint before stopping services owned by these scripts.
foreach ($role in @('tunnel', 'preview', 'backend')) {
    foreach ($entry in @($entries | Where-Object { $_.Role -eq $role })) {
        $process = Get-Process -Id $entry.Id -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.StartTicks) {
            Stop-Process -Id $entry.Id -ErrorAction Stop
            Write-Host "Stopped $role (PID $($entry.Id))."
        }
    }
}
Save-DemoProcesses @()
$urlPath = Join-Path $demoLogDir 'public-url.txt'
if (Test-Path -LiteralPath $urlPath) { Remove-Item -LiteralPath $urlPath }
Write-Host 'Demo stopped. Previously running services were left unchanged.'
