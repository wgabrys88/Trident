param(
    [string]$CudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6",
    [string]$Generator = "Visual Studio 17 2022",
    [string]$Arch = "x64",
    [string]$Toolset = "cuda=12.6",
    [string[]]$Def = @()
)
$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot "detect_cpu.ps1")

$nvcc = Join-Path $CudaRoot "bin\nvcc.exe"
if (-not (Test-Path $nvcc)) {
    Write-Error "CUDA nvcc not found at $nvcc"
}
$env:CUDA_PATH = $CudaRoot
$env:CUDA_PATH_V12_6 = $CudaRoot

$build = Join-Path $gemmaRoot "build"
$cmakeArgs = @(
    "-S", $gemmaRoot,
    "-B", $build,
    "-G", $Generator,
    "-A", $Arch,
    "-T", $Toolset,
    "-DCMAKE_CUDA_COMPILER=$nvcc",
    "-DCMAKE_CUDA_TOOLKIT_ROOT_DIR=$CudaRoot",
    "-DCMAKE_BUILD_TYPE=Release"
)
foreach ($item in $Def) { $cmakeArgs += "-D$item" }
cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "configure ok - build: cmake --build $build --config Release --target gemma-brain -j"
