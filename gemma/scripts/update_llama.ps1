$ErrorActionPreference = "Stop"
$repo = Join-Path (Split-Path $PSScriptRoot -Parent) "llama.cpp"
if (-not (Test-Path (Join-Path $repo ".git"))) {
    Write-Error "missing $repo — run: git clone https://github.com/ggml-org/llama.cpp.git $repo"
}
Push-Location $repo
git fetch origin master
git checkout master
git pull --ff-only origin master
Write-Host "llama.cpp at $(git log -1 --oneline)"
Pop-Location
