$ErrorActionPreference = "Stop"

$wd = Split-Path -Parent $MyInvocation.MyCommand.Path
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
    try {
        $rows = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$pattern*" }
        return ($rows -and $rows.Count -gt 0)
    } catch {
        return $false
    }
}

if (!(Is-Running "monitor.py run")) {
    $p = Start-Process -FilePath $pythonExe -ArgumentList @("-u", "monitor.py", "run") -WorkingDirectory $wd -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "monitor.out.log") -RedirectStandardError (Join-Path $logs "monitor.err.log") -PassThru
    Set-Content -Encoding UTF8 (Join-Path $logs "monitor.pid") $p.Id
}

if (!(Is-Running "feishu_status_bot.py")) {
    $p = Start-Process -FilePath $pythonExe -ArgumentList @("-u", "feishu_status_bot.py") -WorkingDirectory $wd -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "status_bot.out.log") -RedirectStandardError (Join-Path $logs "status_bot.err.log") -PassThru
    Set-Content -Encoding UTF8 (Join-Path $logs "status_bot.pid") $p.Id
}

if (!(Get-Process cloudflared -ErrorAction SilentlyContinue)) {
    $p = Start-Process -FilePath $cloudflared -ArgumentList "tunnel --url http://localhost:8088" -WorkingDirectory $wd -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "cloudflared.out.log") -RedirectStandardError (Join-Path $logs "cloudflared.err.log") -PassThru
    Set-Content -Encoding UTF8 (Join-Path $logs "cloudflared.pid") $p.Id
}

Write-Output "START_OK"
