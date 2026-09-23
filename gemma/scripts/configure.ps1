$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot "detect_cpu.ps1")

$cudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6"
$nvcc = Join-Path $cudaRoot "bin\nvcc.exe"
if (-not (Test-Path $nvcc)) {
    Write-Error "CUDA 12.6 nvcc not found at $nvcc (install toolkit first)."
}
$env:CUDA_PATH = $cudaRoot
$env:CUDA_PATH_V12_6 = $cudaRoot

$build = Join-Path $gemmaRoot "build"
cmake -S $gemmaRoot -B $build -G "Visual Studio 17 2022" -A x64 -T "cuda=12.6" `
    -DCMAKE_CUDA_COMPILER="$nvcc" `
    -DCMAKE_CUDA_TOOLKIT_ROOT_DIR="$cudaRoot" `
    -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "configure ok - build: cmake --build $build --config Release --target gemma-brain -j"
