$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$profileDir = Join-Path $repoRoot ".browser-profile"
$port = 9222

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
  "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  throw "Chrome executable not found. Install Google Chrome or update scripts/start_automation_chrome.ps1 with the correct path."
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$args = @(
  "--remote-debugging-address=127.0.0.1",
  "--remote-debugging-port=$port",
  "--remote-allow-origins=http://127.0.0.1:$port",
  "--user-data-dir=$profileDir",
  "--no-first-run",
  "--no-default-browser-check",
  "https://github.com/settings/tokens?type=beta",
  "https://app.tavily.com/"
)

Start-Process -FilePath $chrome -ArgumentList $args -WindowStyle Normal

Write-Host "Started automation Chrome."
Write-Host "Profile: $profileDir"
Write-Host "CDP: http://127.0.0.1:$port"
Write-Host "Run .\scripts\check_automation_chrome.ps1 to verify the connection."
