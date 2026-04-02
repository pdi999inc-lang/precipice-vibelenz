<#
.SYNOPSIS
    VIE Baseline Benchmark Runner

.DESCRIPTION
    Invokes vie_benchmark.py --json, parses results, and renders a formatted
    report with colour-coded pass/fail, grouped sections, progress bar, and
    per-test notes. Mirrors the Python runner output exactly.

.PARAMETER Quick
    Pass --quick to vie_benchmark.py (reduced reps, fast CI smoke test).

.PARAMETER Json
    Emit raw JSON to stdout instead of the formatted report.

.PARAMETER PythonExe
    Path to the Python executable. Defaults to 'python'.

.PARAMETER ScriptPath
    Path to vie_benchmark.py. Defaults to the directory containing this script.

.EXAMPLE
    .\Invoke-VIEBenchmark.ps1
    .\Invoke-VIEBenchmark.ps1 -Quick
    .\Invoke-VIEBenchmark.ps1 -Json
    .\Invoke-VIEBenchmark.ps1 -PythonExe python3 -ScriptPath C:\vibelenz\vie_benchmark.py

.NOTES
    Exit codes:
        0 — all benchmarks passed
        1 — one or more benchmarks failed
        2 — Python or script not found
        3 — benchmark output could not be parsed
#>

[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$Json,
    [string]$PythonExe  = 'python',
    [string]$ScriptPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Resolve script path
# ---------------------------------------------------------------------------

if (-not $ScriptPath) {
    $ScriptPath = Join-Path $PSScriptRoot 'vie_benchmark.py'
}

if (-not (Test-Path $ScriptPath)) {
    Write-Error "vie_benchmark.py not found at: $ScriptPath"
    exit 2
}

# ---------------------------------------------------------------------------
# Check Python
# ---------------------------------------------------------------------------

try {
    $null = & $PythonExe --version 2>&1
} catch {
    Write-Error "Python executable not found: '$PythonExe'"
    exit 2
}

# ---------------------------------------------------------------------------
# Build argument list and invoke
# ---------------------------------------------------------------------------

$pyArgs = @($ScriptPath, '--json')
if ($Quick) { $pyArgs += '--quick' }

$rawOutput = & $PythonExe @pyArgs 2>&1
$exitCode  = $LASTEXITCODE

# If --Json flag, just relay output and exit
if ($Json) {
    $rawOutput | Where-Object { $_ -is [string] } | Write-Output
    exit $exitCode
}

# ---------------------------------------------------------------------------
# Parse JSON output
# ---------------------------------------------------------------------------

$stdout = ($rawOutput | Where-Object { $_ -is [string] }) -join "`n"

try {
    $data = $stdout | ConvertFrom-Json
} catch {
    Write-Host ""
    Write-Host "  [ERROR] Could not parse benchmark output as JSON." -ForegroundColor Red
    Write-Host "  Raw output:" -ForegroundColor Red
    Write-Host $stdout
    exit 3
}

$results   = $data.results
$summary   = $data.summary
$meta      = $data.meta
$passCount = $summary.passed
$total     = $summary.total

# ---------------------------------------------------------------------------
# Section labels (match Python runner exactly)
# ---------------------------------------------------------------------------

$sectionLabels = [ordered]@{
    'schemas'     = '1. schemas.py — Pydantic model contracts'
    'behavior'    = '2. behavior.py — BehaviorExtractor'
    'dynamics'    = '3. relationship_dynamics.py — RelationshipAnalyzer'
    'analyzer'    = '4. analyzer_combined.py — Deterministic pipeline'
    'interpreter' = '5. interpreter.py — interpret_analysis()'
    'ocr'         = '6. ocr.py — Preprocessing & availability gate'
    'verifier'    = '7. VIE verifier threshold gate (0.9734)'
}

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

$LINE  = '=' * 76
$DIV = "-"  # ─
$PASS  = "v"  # ✓
$FAIL  = "x"  # ✗
$BLOCK = "#"  # █
$LIGHT = "."  # ░
$NAME_W = 40
$VAL_W  = 18
$TGT_W  = 22

function Pad([string]$s, [int]$w) {
    if ($s.Length -ge $w) { return $s.Substring(0, $w) }
    return $s.PadRight($w)
}

# ---------------------------------------------------------------------------
# Render report
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $LINE
Write-Host "  VIE Baseline Benchmarks — Real Contract Tests"
Write-Host "  $($meta.timestamp)  [mode: $($meta.mode)  python: $($meta.python)]"
Write-Host $LINE

$currentGroup = $null

foreach ($r in $results) {

    # Determine group prefix from test name
    $prefix = ($r.name -split '_')[0]

    if ($prefix -ne $currentGroup) {
        $currentGroup = $prefix
        $label = if ($sectionLabels.Contains($prefix)) { $sectionLabels[$prefix] } else { $prefix }
        Write-Host ""
        Write-Host "  $label"
        Write-Host "  $($DIV * 70)"
    }

    $icon      = if ($r.passed) { $PASS } else { $FAIL }
    $iconColor = if ($r.passed) { 'Green' } else { 'Red' }
    $nameCol   = Pad $r.name  $NAME_W
    $valCol    = Pad ([string]$r.value)  $VAL_W
    $tgtCol    = Pad ([string]$r.target) $TGT_W

    Write-Host -NoNewline "  "
    Write-Host -NoNewline "$icon " -ForegroundColor $iconColor
    Write-Host -NoNewline "$nameCol  $valCol  $tgtCol  "
    Write-Host $r.unit

    if ($r.notes) {
        $indent = "       " + (" " * $NAME_W) + "  "
        Write-Host "$indent$($r.notes)" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host $LINE

$barFilled = $BLOCK * $passCount
$barEmpty  = $LIGHT * ($total - $passCount)
$bar       = "[$barFilled$barEmpty]"
$pct       = [math]::Round(($passCount / $total) * 100)

if ($passCount -eq $total) {
    $statusLabel = "BASELINE LOCKED $PASS"
    $statusColor = 'Green'
} else {
    $statusLabel = 'REVIEW NEEDED'
    $statusColor = 'Yellow'
}

Write-Host -NoNewline "  "
Write-Host -NoNewline "$statusLabel  " -ForegroundColor $statusColor
Write-Host "$passCount/$total  $bar  $pct%"
Write-Host $LINE
Write-Host ""

# ---------------------------------------------------------------------------
# Failed test summary (if any)
# ---------------------------------------------------------------------------

$failed = $results | Where-Object { -not $_.passed }

if ($failed) {
    Write-Host "  Failed tests:" -ForegroundColor Red
    foreach ($f in $failed) {
        Write-Host "    $FAIL  $($f.name)" -ForegroundColor Red
        Write-Host "         got:    $($f.value)" -ForegroundColor DarkGray
        Write-Host "         target: $($f.target)" -ForegroundColor DarkGray
        if ($f.notes) {
            Write-Host "         notes:  $($f.notes)" -ForegroundColor DarkGray
        }
    }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Exit code mirrors Python runner: 0 = all pass, 1 = any fail
# ---------------------------------------------------------------------------

exit $exitCode


