# Shared local process tracking. Never stop an untracked process or a reused PID.
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoLogDir = Join-Path $demoRoot 'logs/public-demo'
$demoStatePath = Join-Path $demoLogDir 'processes.json'
$demoNgrok = Join-Path $demoRoot '.cache/ngrok/ngrok.exe'
New-Item -ItemType Directory -Force -Path $demoLogDir | Out-Null

function Get-DemoProcesses {
    if (-not (Test-Path -LiteralPath $demoStatePath)) { return }
    foreach ($entry in @(Get-Content -LiteralPath $demoStatePath -Raw | ConvertFrom-Json)) {
        if ($null -eq $entry -or $null -eq $entry.Id) { continue }
        $process = Get-Process -Id $entry.Id -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.StartTicks) {
            $entry
        }
    }
}

function Save-DemoProcesses($entries) {
    ConvertTo-Json -InputObject @($entries) | Set-Content -LiteralPath $demoStatePath -Encoding UTF8
}

function Start-DemoProcess([string]$role, [string]$file, [string[]]$arguments, [string]$directory) {
    $entries = @(Get-DemoProcesses)
    $process = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $directory `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $demoLogDir "$role.out.log") `
        -RedirectStandardError (Join-Path $demoLogDir "$role.err.log")
    $entry = [pscustomobject]@{
        Role = $role
        Id = $process.Id
        StartTicks = $process.StartTime.ToUniversalTime().Ticks.ToString()
    }
    Save-DemoProcesses ($entries + $entry)
    $process
}

function Get-DemoResponse([string]$url) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -Proxy $null }
    catch { return $null }
}

function Wait-DemoService([string]$url, [System.Diagnostics.Process]$process) {
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if ($process.HasExited) { throw "Service exited. See $demoLogDir" }
        $response = Get-DemoResponse $url
        if ($response -and $response.StatusCode -eq 200) { return $response }
        Start-Sleep -Milliseconds 500
    }
    throw "Service is not ready at $url. See $demoLogDir"
}

function Assert-DemoPortFree([int]$port) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is occupied by another service. No process was stopped."
    }
}
