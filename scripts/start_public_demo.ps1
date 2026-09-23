param([switch]$LocalOnly)
. "$PSScriptRoot/public_demo_common.ps1"

if (-not $LocalOnly) {
    if (-not (Test-Path -LiteralPath $demoNgrok)) { throw 'ngrok is missing from .cache/ngrok/ngrok.exe.' }
    & $demoNgrok config check
    if ($LASTEXITCODE -ne 0) { throw 'Run scripts/configure_ngrok.ps1 first to configure your account.' }
}

$frontendDir = Join-Path $demoRoot 'frontend'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$python = Join-Path $demoRoot '.venv-react/Scripts/python.exe'
$vite = Join-Path $frontendDir 'node_modules/vite/bin/vite.js'
if (-not (Test-Path -LiteralPath $vite)) { throw 'Run npm.cmd ci in frontend first.' }

Write-Host 'Building the demo frontend...'
Push-Location $frontendDir
try {
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }

$backend = Get-DemoResponse 'http://127.0.0.1:8000/openapi.json'
if (-not $backend) {
    Assert-DemoPortFree 8000
    if (-not (Test-Path -LiteralPath $python)) { throw 'The .venv-react Python environment is missing.' }
    Write-Host 'Starting the backend...'
    $backendProcess = Start-DemoProcess 'backend' $python @('-m', 'uvicorn', 'service.api:app', '--host', '127.0.0.1', '--port', '8000') $demoRoot
    $backend = Wait-DemoService 'http://127.0.0.1:8000/openapi.json' $backendProcess
}
if (($backend.Content | ConvertFrom-Json).info.title -ne 'Bidding Assistant Streaming API') {
    throw 'Port 8000 is not the expected Agent backend.'
}

$preview = Get-DemoResponse 'http://127.0.0.1:4173/'
if (-not $preview) {
    Assert-DemoPortFree 4173
    Write-Host 'Starting the demo preview...'
    $previewProcess = Start-DemoProcess 'preview' $node @(('"{0}"' -f $vite), 'preview') $frontendDir
    $preview = Wait-DemoService 'http://127.0.0.1:4173/' $previewProcess
}
if ($preview.Headers['X-Bidding-Demo'] -ne '1') { throw 'Port 4173 is not the expected demo preview.' }
Write-Host 'Local demo: http://127.0.0.1:4173/'

if ($LocalOnly) {
    Write-Host 'Local checks complete. To publish, run this script again without -LocalOnly.'
    return
}

$tunnelStatus = Get-DemoResponse 'http://127.0.0.1:4040/api/tunnels'
if ($tunnelStatus) {
    $ownedTunnel = @(Get-DemoProcesses | Where-Object { $_.Role -eq 'tunnel' })
    if (-not $ownedTunnel) { throw 'An existing ngrok agent is using port 4040. It was left unchanged.' }
} else {
    Assert-DemoPortFree 4040
    Write-Host 'Connecting ngrok...'
    $tunnelProcess = Start-DemoProcess 'tunnel' $demoNgrok @('http', 'http://127.0.0.1:4173', '--host-header=rewrite', '--inspect=false', '--log=stdout', '--log-format=json') $demoRoot
}

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($tunnelProcess -and $tunnelProcess.HasExited) { throw "ngrok exited. Check $demoLogDir/tunnel.err.log and tunnel.out.log." }
    $tunnelStatus = Get-DemoResponse 'http://127.0.0.1:4040/api/tunnels'
    if ($tunnelStatus) {
        $tunnel = ($tunnelStatus.Content | ConvertFrom-Json).tunnels | Where-Object {
            $_.public_url -like 'https://*' -and $_.config.addr -eq 'http://127.0.0.1:4173'
        } | Select-Object -First 1
        if ($tunnel) {
            $tunnel.public_url | Set-Content -LiteralPath (Join-Path $demoLogDir 'public-url.txt') -Encoding UTF8
            Write-Host "Public demo: $($tunnel.public_url)"
            Write-Host 'Keep this computer awake and online. Stop with scripts/stop_public_demo.ps1.'
            return
        }
    }
    Start-Sleep -Milliseconds 500
}
throw "ngrok has not reported a public URL yet. See $demoLogDir/tunnel.out.log."
