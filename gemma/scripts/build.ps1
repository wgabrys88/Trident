$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot "configure.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$cudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6"
$env:CUDA_PATH = $cudaRoot
$env:CUDA_PATH_V12_6 = $cudaRoot

$build = Join-Path $gemmaRoot "build"
# ggml-cuda: single job on Windows — parallel nvcc often hits C1083 (Permission denied on .obj).
$cleanCuda = $false
if ($args -contains "-CleanCuda") { $cleanCuda = $true }
$cudaBuild = @("--build", $build, "--config", "Release", "--target", "ggml-cuda", "--parallel", "1")
if ($cleanCuda) { $cudaBuild += "--clean-first" }
cmake @cudaBuild
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build $build --config Release --target gemma-brain --parallel 4
exit $LASTEXITCODE
