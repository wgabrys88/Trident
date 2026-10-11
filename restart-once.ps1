[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TridentRoot,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z]+$')][string]$Engine,
    [ValidateRange(0, 3600)][int]$DelaySeconds = 5,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $TridentRoot).Path
$python = (Get-Command py.exe -ErrorAction Stop).Source
$runScript = Join-Path $root 'run.py'
if (-not (Test-Path -LiteralPath $runScript -PathType Leaf)) { throw 'Missing run.py' }
Push-Location -LiteralPath $root
try {
    & $python -3.11 -B -c "import sys; from contextlib import ExitStack; from bus import Config; c=Config(); c['mind'][sys.argv[1]]; stack=ExitStack(); stack.enter_context(c.lock(c.root/'.supply.lock')); [stack.enter_context(c.lock(p)) for p in (c.root/'wire').glob('*/owner.lock')]; stack.close()" $Engine
    if ($LASTEXITCODE -ne 0) { throw 'Trident ownership or configuration unavailable; stop all Trident processes first.' }
    if (-not $Install) { throw 'This helper only schedules a start with -Install; it never stops Trident or reboots Windows.' }
    $start = (Get-Date).AddSeconds($DelaySeconds)
    $trigger = New-ScheduledTaskTrigger -Once -At $start
    $trigger.EndBoundary = $start.AddMinutes(5).ToString('o')
    $action = New-ScheduledTaskAction -Execute $python -Argument "-3.11 -B `"$runScript`" $Engine" -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'Trident-OneShot-Start' -Action $action -Trigger $trigger -Settings $settings -Description 'Start one stopped Trident instance; no shutdown or Windows reboot.' -Force -ErrorAction Stop
} finally { Pop-Location }
