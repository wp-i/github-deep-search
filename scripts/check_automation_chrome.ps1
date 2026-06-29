$ErrorActionPreference = "Stop"

$port = 9222
$baseUrl = "http://127.0.0.1:$port"

try {
  $version = Invoke-RestMethod -Uri "$baseUrl/json/version" -TimeoutSec 3
  $tabs = Invoke-RestMethod -Uri "$baseUrl/json/list" -TimeoutSec 3
} catch {
  Write-Host "Chrome CDP is not reachable at $baseUrl"
  Write-Host "Start it with: .\scripts\start_automation_chrome.ps1"
  exit 1
}

Write-Host "CDP reachable."
Write-Host "Browser: $($version.Browser)"
Write-Host "Protocol: $($version.'Protocol-Version')"
Write-Host ""
Write-Host "Open tabs:"
foreach ($tab in $tabs) {
  Write-Host "- $($tab.title) <$($tab.url)>"
}

