$ErrorActionPreference = "Stop"

$wd = "C:\Users\fab\browser-review-monitor"
$logs = Join-Path $wd "logs"
if (!(Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs | Out-Null
}

$cloudflared = "C:\Users\fab\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
if (!(Test-Path $cloudflared)) {
    throw "cloudflared not found at: $cloudflared"
}

function Get-PythonExe() {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    foreach ($candidate in @(
        "C:\Python314\python.exe",
        "C:\Users\fab\AppData\Local\Programs\Python\Python312\python.exe"
    )) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    throw "python executable not found"
}

$pythonExe = Get-PythonExe

function Is-Running([string]$pattern) {
    $rows = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$pattern*" }
    return ($rows -and $rows.Count -gt 0)
}

if (!(Is-Running "monitor.py run")) {
    Start-Process -FilePath $pythonExe `
        -ArgumentList @("-u", "monitor.py", "run") `
        -WorkingDirectory $wd `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "monitor.out.log") `
        -RedirectStandardError (Join-Path $logs "monitor.err.log") | Out-Null
}

if (!(Is-Running "feishu_status_bot.py")) {
    Start-Process -FilePath $pythonExe `
        -ArgumentList @("-u", "feishu_status_bot.py") `
        -WorkingDirectory $wd `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "status_bot.out.log") `
        -RedirectStandardError (Join-Path $logs "status_bot.err.log") | Out-Null
}

if (!(Get-Process cloudflared -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $cloudflared `
        -ArgumentList @("tunnel", "--url", "http://localhost:8088") `
        -WorkingDirectory $wd `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "cloudflared.out.log") `
        -RedirectStandardError (Join-Path $logs "cloudflared.err.log") | Out-Null
}

# Extract latest quick-tunnel URL for easy copy/paste into Feishu callback.
function Get-LatestTunnelUrl {
    $pattern = "https://[a-z0-9-]+\.trycloudflare\.com"
    $candidates = @()
    foreach ($log in @((Join-Path $logs "cloudflared.err.log"), (Join-Path $logs "cloudflared.out.log"))) {
        if (Test-Path $log) {
            $tail = Get-Content $log -Tail 1000
            foreach ($line in $tail) {
                $m = [regex]::Match($line, $pattern)
                if ($m.Success) {
                    $candidates += $m.Value
                }
            }
        }
    }
    if ($candidates.Count -gt 0) {
        return $candidates[-1]
    }
    return ""
}

$urlFile = Join-Path $wd "data\\current_tunnel_url.txt"
$latestUrl = Get-LatestTunnelUrl
if ($latestUrl) {
    Set-Content -Encoding UTF8 $urlFile ($latestUrl + "/")
} else {
    # Avoid stale callback URL from old tunnel sessions.
    Set-Content -Encoding UTF8 $urlFile ""
}

Write-Output "ENSURE_OK"
