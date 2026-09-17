$ErrorActionPreference = "Stop"
$Parent = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Parent "chatterbox.cpp"
$EngineUrl = "https://github.com/wgabrys88/chatterbox.cpp.git"
$Common = Join-Path $PSScriptRoot "tts_common.py"
$Pin = Select-String -Path $Common -Pattern 'ENGINE_REV = "([0-9a-f]{40})"'
if (-not $Pin) {
    throw "ENGINE_REV missing from tts_common.py"
}
$Commit = $Pin.Matches[0].Groups[1].Value
if ($args.Count -gt 0 -and $args[0]) {
    $Commit = $args[0]
}

if (-not (Test-Path $Engine)) {
    git clone $EngineUrl $Engine
}
if (-not (Test-Path (Join-Path $Engine ".git"))) {
    throw "$Engine exists but is not a Git repository."
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}

git -C $Engine cat-file -e "$Commit^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    git -C $Engine fetch origin $Commit
}
git -C $Engine checkout --detach $Commit

$Actual = (git -C $Engine rev-parse HEAD).Trim()
if ($Actual -ne $Commit) {
    throw "Engine SHA mismatch: $Actual != $Commit"
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}
Write-Host "Engine ready at $Actual"
Write-Host "Copy the original voice prompt to $(Join-Path $PSScriptRoot 'reference.wav') before synthesis."
