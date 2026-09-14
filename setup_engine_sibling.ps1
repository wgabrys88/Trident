$ErrorActionPreference = "Stop"
$Parent = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Parent "chatterbox.cpp"
$EngineUrl = "https://github.com/wgabrys88/chatterbox.cpp.git"
$EngineCommit = "dc0e83d2666d6f0ce5560466b9da737467cf47fa"

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
