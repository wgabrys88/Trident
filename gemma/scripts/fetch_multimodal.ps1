$ErrorActionPreference = "Stop"
$base = "https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main"
$dest = Join-Path (Split-Path $PSScriptRoot -Parent) "models"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$files = @(
    "gemma-4-E2B-it-Q4_0.gguf",
    "mmproj-gemma-4-E2B-it-BF16.gguf"
)
foreach ($name in $files) {
    $out = Join-Path $dest $name
    if (Test-Path $out) {
        Write-Host "skip $name"
        continue
    }
    Write-Host "download $name"
    curl.exe -fL -o $out "$base/$name"
}
