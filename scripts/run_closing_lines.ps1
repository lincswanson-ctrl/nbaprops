# run_closing_lines.ps1
#
# Captures closing-line snapshots for every player prop in the 5-minute window
# before each game's tipoff.  Run this in the SAME PowerShell session as
# run_pregame_odds.ps1 so that the DRAFTKINGS_EVENT_SUBCAT_* env vars are already
# set for combo markets.
#
# Start it 10-15 minutes before the first tip — it sleeps until the closing window
# opens, then polls every 30 seconds and upserts.  Exits automatically after all
# games have tipped.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_closing_lines.ps1

$ErrorActionPreference = "Stop"

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -Path ".."

Write-Host "Closing-line capture starting..."
Write-Host "  PRA  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS_ASSISTS"
Write-Host "  P+R  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS"
Write-Host "  P+A  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_ASSISTS"
Write-Host "  R+A  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_REBOUNDS_ASSISTS"
Write-Host ""
Write-Host "Waiting for the 5-minute closing window.  Script exits after last tip."
Write-Host ""

.\.venv_local\Scripts\python.exe -m data_pipeline.scrapers.odds `
    --book all `
    --markets all `
    --closing-lines
