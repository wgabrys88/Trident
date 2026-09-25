param(
    [Parameter(Mandatory = $true)][string]$BuildDir,
    [Parameter(Mandatory = $true)][int]$BrainParallel,
    [Parameter(Mandatory = $true)][int]$CudaCodegenParallel,
    [string]$VulkanSdk = ""
)
$ErrorActionPreference = "Stop"
if (-not $env:TRIDENT_GENERATOR) { Write-Error "TRIDENT_GENERATOR is required" }
if (-not $env:TRIDENT_ARCH) { Write-Error "TRIDENT_ARCH is required" }
if (-not $env:TRIDENT_GEMMA_BACKEND) { Write-Error "TRIDENT_GEMMA_BACKEND is required" }
if (-not $env:TRIDENT_CUDA_MAX) { Write-Error "TRIDENT_CUDA_MAX is required" }
$Backend = $env:TRIDENT_GEMMA_BACKEND
$params = @{
    Generator = $env:TRIDENT_GENERATOR
    Arch = $env:TRIDENT_ARCH
    Backend = $Backend
    CudaMax = $env:TRIDENT_CUDA_MAX
    BuildDir = $BuildDir
}
if ($VulkanSdk) { $params.VulkanSdk = $VulkanSdk }
if ($env:TRIDENT_CUDA_ROOT) { $params.CudaRoot = $env:TRIDENT_CUDA_ROOT }
if ($env:TRIDENT_CUDA_TOOLSET) { $params.Toolset = $env:TRIDENT_CUDA_TOOLSET }
$defs = @($env:TRIDENT_GEMMA_DEFS -split "`n" | Where-Object { $_ })
if ($defs.Count) { $params.Def = $defs }
& (Join-Path $PSScriptRoot "configure.ps1") @params
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Backend -eq "cuda") {
    cmake --build $BuildDir --config Release --target ggml-cuda --parallel $CudaCodegenParallel
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
cmake --build $BuildDir --config Release --target gemma-brain --parallel $BrainParallel
exit $LASTEXITCODE
