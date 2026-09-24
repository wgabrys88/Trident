$ErrorActionPreference = "Stop"
param(
    [string]$CudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6",
    [string]$Generator = "Visual Studio 17 2022",
    [string]$Arch = "x64",
    [string]$Toolset = "cuda=12.6",
    [int]$CudaParallel = 1,
    [int]$BrainParallel = 4,
    [switch]$CleanCuda,
    [string[]]$Def = @()
)
$gemmaRoot = Split-Path $PSScriptRoot -Parent
$configure = @(
    "-CudaRoot", $CudaRoot,
    "-Generator", $Generator,
    "-Arch", $Arch,
    "-Toolset", $Toolset
)
foreach ($item in $Def) { $configure += @("-Def", $item) }
& (Join-Path $PSScriptRoot "configure.ps1") @configure
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:CUDA_PATH = $CudaRoot
$env:CUDA_PATH_V12_6 = $CudaRoot

$build = Join-Path $gemmaRoot "build"
$cudaBuild = @("--build", $build, "--config", "Release", "--target", "ggml-cuda", "--parallel", "$CudaParallel")
if ($CleanCuda) { $cudaBuild += "--clean-first" }
cmake @cudaBuild
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build $build --config Release --target gemma-brain --parallel $BrainParallel
exit $LASTEXITCODE
