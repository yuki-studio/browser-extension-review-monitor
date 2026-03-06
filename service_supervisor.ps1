$ErrorActionPreference = "Continue"
$wd = "C:\Users\fab\browser-review-monitor"

while ($true) {
    try {
        powershell -ExecutionPolicy Bypass -File (Join-Path $wd "ensure_services.ps1") | Out-Null
    } catch {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content -Encoding UTF8 (Join-Path $wd "logs\\supervisor.err.log") "$ts`t$($_.Exception.Message)"
    }
    Start-Sleep -Seconds 30
}
