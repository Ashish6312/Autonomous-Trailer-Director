# Demo: runs five steps in order. Offline; no API key needed.
# Usage:  .\demo.ps1            (pauses between steps)
#         .\demo.ps1 -NoPause   (runs straight through)
param([switch]$NoPause)

$cli = ".venv\Scripts\trailer-director.exe"

function Step($title) {
    if (-not $NoPause -and $script:started) { Read-Host "`nPress Enter for the next step" | Out-Null }
    $script:started = $true
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
}

Step "1. Validate the episode package"
& $cli validate-data 2>$null

Step "2. Three audience plans (export, then the report summary)"
$out = Join-Path $env:TEMP "trailer_demo"   # keeps the committed sample_run untouched
& $cli export --skip-evaluation --out $out 2>$null
Get-Content (Join-Path $out "validation_report.md") -TotalCount 24

Step "3. Verifier rejects a spoiler; only that clip is repaired"
& $cli repair --audience young_adult --mode replay 2>$null

Step "4. The same run as a structured audit trail"
& $cli repair --audience young_adult --mode replay --json 2>$null |
    ConvertFrom-Json |
    Select-Object -ExpandProperty audit_trail |
    Format-Table step, attempt, detail -Wrap

Step "5. Contract amended after approval: only the affected clip changes"
& $cli evaluate --scenario S17_contract_change 2>$null

Write-Host "`nDone. Limits: no episode media analysed, live repair never run, cultural review needs a person." -ForegroundColor Yellow
