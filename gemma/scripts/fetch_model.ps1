$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $gemmaRoot "models"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$file = "gemma-4-E2B-it-Q4_0.gguf"
$url = "https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/$file"
$out = Join-Path $dest $file
if (Test-Path $out) {
    Write-Host "exists $out"
    exit 0
}
Write-Host "downloading $url"
curl.exe -fL -o $out $url
