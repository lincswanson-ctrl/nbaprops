Param(
  [switch]$NoSyntheticGames,
  [switch]$SkipDKComboDiscovery,
  [int]$LookbackHours = 24
)

$ErrorActionPreference = "Stop"

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -Path ".."

if ($NoSyntheticGames) {
  Remove-Item Env:ODDS_ALLOW_SYNTHETIC_GAMES -ErrorAction SilentlyContinue
} else {
  $env:ODDS_ALLOW_SYNTHETIC_GAMES = "1"
}

function Get-DKEventId {
  # Prefer a raw file written in the last 10 minutes (from the points scrape just above).
  $cutoff = (Get-Date).AddMinutes(-10)
  $raw = Get-ChildItem -Path ".\\data\\raw\\odds\\draftkings" -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -ge $cutoff } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($null -ne $raw -and $raw.Name -match '^(\d+)_') { return $Matches[1] }
  return $null
}

function Get-DKEventIdFromScraper {
  $tmp = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'
  Set-Content $tmp -Encoding utf8 -Value @(
    "import asyncio, sys",
    "sys.path.insert(0, '.')",
    "from data_pipeline.scrapers.odds.draftkings import DraftKingsScraper",
    "async def main():",
    "    async with DraftKingsScraper() as dk:",
    "        events = await dk.list_events()",
    "        if events: print(events[0].bookmaker_event_id)",
    "asyncio.run(main())"
  )
  $id = .\.venv_local\Scripts\python.exe $tmp
  Remove-Item $tmp -ErrorAction SilentlyContinue
  return ($id | Select-Object -Last 1).Trim()
}

if (-not $SkipDKComboDiscovery) {
  .\.venv_local\Scripts\python.exe -m data_pipeline.scrapers.odds --book draftkings --markets player_points --once | Out-Host

  $eventId = Get-DKEventId
  if ($null -eq $eventId) {
    Write-Host "No fresh DK raw file found; falling back to list_events() for event ID..."
    $eventId = Get-DKEventIdFromScraper
  }
  if ($null -ne $eventId -and $eventId -ne "") {
    $rawOutput = .\.venv_local\Scripts\python.exe .\scripts\dk_discover_combo_event_subcats.py --event-id $eventId --min-id 10000 --max-id 90000 --step 1 --concurrency 25
    # Extract only the JSON line — httpx can leak INFO lines to stdout before it
    $json = ($rawOutput | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1)
    $obj = $json | ConvertFrom-Json
    if ($obj.discovered.player_points_rebounds_assists) {
      $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS_ASSISTS = $obj.discovered.player_points_rebounds_assists.event_subcategory_id
    }
    if ($obj.discovered.player_points_rebounds) {
      $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS = $obj.discovered.player_points_rebounds.event_subcategory_id
    }
    if ($obj.discovered.player_points_assists) {
      $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_ASSISTS = $obj.discovered.player_points_assists.event_subcategory_id
    }
    if ($obj.discovered.player_rebounds_assists) {
      $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_REBOUNDS_ASSISTS = $obj.discovered.player_rebounds_assists.event_subcategory_id
    }

    Write-Host "DK combo subcats discovered for event ${eventId}:"
    Write-Host "  PRA  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS_ASSISTS"
    Write-Host "  P+R  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_REBOUNDS"
    Write-Host "  P+A  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_POINTS_ASSISTS"
    Write-Host "  R+A  = $env:DRAFTKINGS_EVENT_SUBCAT_PLAYER_REBOUNDS_ASSISTS"
  } else {
    Write-Host "No DK raw files found; skipping DK combo discovery."
  }
}

.\\.venv_local\\Scripts\\python.exe -m data_pipeline.scrapers.odds --book all --markets all --once | Out-Host
.\\.venv_local\\Scripts\\python.exe .\\scripts\\validate_odds_sources.py --lookback-hours $LookbackHours | Out-Host
