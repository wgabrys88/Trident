$ErrorActionPreference = "Stop"
$Parent = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Parent "chatterbox.cpp"
$EngineUrl = "https://github.com/wgabrys88/chatterbox.cpp.git"
$EngineCommit = "24138b6924bfae30f4e616559ae63948b3889695"

if (-not (Test-Path $Engine)) {
    git clone -b experimental $EngineUrl $Engine
}
if (-not (Test-Path (Join-Path $Engine ".git"))) {
    throw "$Engine exists but is not a Git repository."
}

git -C $Engine fetch origin $EngineCommit
git -C $Engine checkout --detach $EngineCommit
$Actual = (git -C $Engine rev-parse HEAD).Trim()
if ($Actual -ne $EngineCommit) {
    throw "Engine SHA mismatch: $Actual != $EngineCommit"
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}
Write-Host "Engine ready at $Actual"
Write-Host "Copy the original voice prompt to $(Join-Path $PSScriptRoot 'reference.wav') before synthesis."
