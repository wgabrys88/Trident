$ErrorActionPreference = "Stop"
$Parent = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $Parent "chatterbox.cpp"
$EngineUrl = "https://github.com/wgabrys88/chatterbox.cpp.git"
$Commit = $args[0]

if (-not (Test-Path $Engine)) {
    git clone -b experimental $EngineUrl $Engine
}
if (-not (Test-Path (Join-Path $Engine ".git"))) {
    throw "$Engine exists but is not a Git repository."
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}

if ($Commit) {
    git -C $Engine cat-file -e "$Commit^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        git -C $Engine fetch origin $Commit
    }
    git -C $Engine checkout --detach $Commit
} else {
    git -C $Engine checkout experimental
}

$Actual = (git -C $Engine rev-parse HEAD).Trim()
if ($Commit -and $Actual -ne $Commit) {
    throw "Engine SHA mismatch: $Actual != $Commit"
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}
Write-Host "Engine ready at $Actual"
Write-Host "Copy the original voice prompt to $(Join-Path $PSScriptRoot 'reference.wav') before synthesis."
