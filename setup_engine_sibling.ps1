$ErrorActionPreference = "Stop"
$Parent = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Parent "chatterbox.cpp"
$EngineUrl = "https://github.com/wgabrys88/chatterbox.cpp.git"
$EngineCommit = "f645f119b4c6f2fb8002fc4e7707c226140db94e"

if (-not (Test-Path $Engine)) {
    git clone -b experimental $EngineUrl $Engine
}
if (-not (Test-Path (Join-Path $Engine ".git"))) {
    throw "$Engine exists but is not a Git repository."
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}

# A full delivery archive already contains the exact engine commit locally.
# Use it without requiring the commit to have been published yet. If the object
# is absent, fetch that exact commit from origin and fail normally if origin does
# not contain it.
git -C $Engine cat-file -e "$EngineCommit^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    git -C $Engine fetch origin $EngineCommit
}
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
