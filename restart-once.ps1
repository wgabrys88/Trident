[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TridentRoot,

    [Parameter(Mandatory = $true)]
    [string]$Engine,

    [int]$DelaySeconds = 5,

    [switch]$Install
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $TridentRoot -PathType Container)) {
    throw "Trident root does not exist: $TridentRoot"
}

$runScript = Join-Path $TridentRoot 'run.py'
if (-not (Test-Path -LiteralPath $runScript -PathType Leaf)) {
    throw "Trident entrypoint does not exist: $runScript"
}

if ($DelaySeconds -lt 0) {
    throw 'DelaySeconds must be non-negative.'
}

$taskName = 'Trident-OneShot-Reboot'
$scriptPath = $MyInvocation.MyCommand.Path
$python = (Get-Command py.exe -ErrorAction SilentlyContinue)
if ($null -eq $python) {
    $python = Get-Command python.exe -ErrorAction Stop
}

if ($Install) {
    $start = (Get-Date).AddSeconds($DelaySeconds)
    $trigger = New-ScheduledTaskTrigger -Once -At $start
    $action = New-ScheduledTaskAction -Execute $python.Source `
        -Argument "-B `"$runScript`" `"$Engine`""
    $settings = New-ScheduledTaskSettingsSet -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 12)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'One-shot controlled Trident restart.' `
        -Force | Out-Null
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
    exit 0
}

& $python.Source -B $runScript $Engine
exit $LASTEXITCODE
