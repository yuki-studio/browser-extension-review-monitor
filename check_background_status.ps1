$wd = Split-Path -Parent $MyInvocation.MyCommand.Path
$logs = Join-Path $wd "logs"

Write-Output "=== Process Check ==="
try {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match "python|cloudflared" -and ($_.CommandLine -like "*$wd*" -or $_.Name -eq "cloudflared.exe") } |
        Select-Object ProcessId, Name, CommandLine
} catch {
    Write-Output "Get-CimInstance unavailable; skipped detailed process check."
}

Write-Output "=== Port Check ==="
try {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", 8088)
    $c.Dispose()
    Write-Output "8088 OPEN"
} catch {
    Write-Output "8088 CLOSED"
}

Write-Output "=== Tunnel URL (last known) ==="
$errLog = Join-Path $logs "cloudflared.err.log"
if (Test-Path $errLog) {
    Get-Content $errLog -Tail 200 | Select-String -Pattern "https://.*trycloudflare.com" | Select-Object -Last 1 | ForEach-Object { $_.Line }
}
$urlFile = Join-Path $wd "data\current_tunnel_url.txt"
if (Test-Path $urlFile) {
    Write-Output "=== Tunnel URL (normalized) ==="
    Get-Content $urlFile
}
