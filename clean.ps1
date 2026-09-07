param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (!(Test-Path -LiteralPath (Join-Path $root '.git'))) { throw 'Run the cleaner from the Trident checkout.' }
# Keep tracked source (including local edits), Git metadata, and downloaded models.
$tracked = @(& git -C $root ls-files)
if ($LASTEXITCODE -ne 0 -or $tracked -notcontains 'main.py') { throw 'Cannot identify Trident source files.' }
$keep = @('.git', 'models', 'clean.ps1') + @($tracked | ForEach-Object { ($_ -split '/')[0] })
$targets = @(Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -notin $keep })
# Validate all targets before deleting anything; never follow junctions or symlinks.
foreach ($target in $targets) {
    $resolved = (Resolve-Path -LiteralPath $target.FullName).Path
    if ([IO.Path]::GetDirectoryName($resolved) -ne $root) { throw "Outside workspace: $resolved" }
    if ($target.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point: $resolved" }
    if ($target.PSIsContainer -and @(Get-ChildItem -LiteralPath $resolved -Force -Recurse -Attributes ReparsePoint).Count) {
        throw "Reparse point inside $resolved"
    }
}
if (!$DryRun) {
    & python (Join-Path $root 'main.py') --unload
    if ($LASTEXITCODE -ne 0) { throw 'Server unload failed; nothing removed.' }
    foreach ($name in @('main.log', 'tts.log')) {
        $path = Join-Path $root $name
        if ((Test-Path -LiteralPath $path) -and $path -notin $targets.FullName) {
            $item = Get-Item -LiteralPath $path -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point: $path" }
            $targets += $item
        }
    }
}
foreach ($target in $targets) {
    Write-Host "Remove $($target.FullName)"
    if (!$DryRun) { Remove-Item -LiteralPath $target.FullName -Recurse -Force }
}
Write-Host 'Source and models preserved. Next: python main.py'
