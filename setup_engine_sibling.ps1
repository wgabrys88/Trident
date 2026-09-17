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

if (-not (Test-Path $Engine)) {
    git clone $EngineUrl $Engine
}
if (-not (Test-Path (Join-Path $Engine ".git"))) {
    throw "$Engine exists but is not a Git repository."
}
if (git -C $Engine status --porcelain) {
    throw "Engine checkout is dirty."
}

$Actual = (git -C $Engine rev-parse HEAD).Trim()
if ($Actual -ne $Commit) {
    throw "chatterbox.cpp HEAD $Actual != ENGINE_REV $Commit. Clone or pull origin/main so it matches Trident/tts_common.py."
}
Write-Host "Engine ready at $Actual"
Write-Host "Copy the original voice prompt to $(Join-Path $PSScriptRoot 'reference.wav') before synthesis."
