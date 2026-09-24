param(
    [string]$VulkanSdk = "",
    [int]$BrainParallel = 4
)
$ErrorActionPreference = "Stop"
$Generator = if ($env:TRIDENT_GENERATOR) { $env:TRIDENT_GENERATOR } else { "Visual Studio 17 2022" }
$Arch = if ($env:TRIDENT_ARCH) { $env:TRIDENT_ARCH } else { "x64" }
$gemmaRoot = Split-Path $PSScriptRoot -Parent
$Backend = if ($env:TRIDENT_GEMMA_BACKEND) { $env:TRIDENT_GEMMA_BACKEND } else { "vulkan" }
$params = @{ Generator = $Generator; Arch = $Arch; Backend = $Backend }
if ($VulkanSdk) { $params.VulkanSdk = $VulkanSdk }
if ($env:TRIDENT_CUDA_ROOT) { $params.CudaRoot = $env:TRIDENT_CUDA_ROOT }
if ($env:TRIDENT_CUDA_TOOLSET) { $params.Toolset = $env:TRIDENT_CUDA_TOOLSET }
$defs = @($env:TRIDENT_GEMMA_DEFS -split "`n" | Where-Object { $_ })
if ($defs.Count) { $params.Def = $defs }
& (Join-Path $PSScriptRoot "configure.ps1") @params
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$build = Join-Path $gemmaRoot "build"
if ($Backend -eq "cuda") {
    cmake --build $build --config Release --target ggml-cuda --parallel 1
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
cmake --build $build --config Release --target gemma-brain --parallel $BrainParallel
exit $LASTEXITCODE
