param(
    [Parameter(Mandatory = $true)][string]$Backend,
    [Parameter(Mandatory = $true)][string]$Generator,
    [Parameter(Mandatory = $true)][string]$Arch,
    [Parameter(Mandatory = $true)][string]$CudaMax,
    [Parameter(Mandatory = $true)][string]$BuildDir,
    [string]$VulkanSdk = "",
    [string]$CudaRoot = "",
    [string]$Toolset = "",
    [string[]]$Def = @()
)
$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent

$cmakeArgs = @(
    "-S", $gemmaRoot,
    "-B", $BuildDir,
    "-G", $Generator,
    "-A", $Arch,
    "-DCMAKE_BUILD_TYPE=Release",
    "-DGEMMA_BACKEND=$Backend"
)
if ($Backend -eq "cuda") {
    if (-not $CudaRoot) { Write-Error "CUDA root is required" }
    if (-not $Toolset) { Write-Error "CUDA toolset is required" }
    $nvcc = Join-Path $CudaRoot "bin\nvcc.exe"
    if (-not (Test-Path $nvcc)) { Write-Error "CUDA nvcc not found at $nvcc" }
    $banner = & $nvcc --version | Out-String
    $limit = $CudaMax.Split(".")
    $maxMajor = [int]$limit[0]
    $maxMinor = [int]$limit[1]
    if ($banner -match "release (\d+)\.(\d+)" -and ([int]$Matches[1] -gt $maxMajor -or ([int]$Matches[1] -eq $maxMajor -and [int]$Matches[2] -gt $maxMinor))) {
        Write-Error "CUDA $($Matches[1]).$($Matches[2]) is above install.cuda_max $CudaMax"
    }
    $env:CUDA_PATH = $CudaRoot
    $cmakeArgs += @(
        "-T", $Toolset,
        "-DCMAKE_CUDA_COMPILER=$nvcc",
        "-DCMAKE_CUDA_TOOLKIT_ROOT_DIR=$CudaRoot"
    )
} elseif ($Backend -eq "vulkan") {
    if (-not $VulkanSdk) { Write-Error "Vulkan SDK is required" }
    $env:VULKAN_SDK = $VulkanSdk
    $cmakeArgs += @(
        "-DCMAKE_PREFIX_PATH=$VulkanSdk",
        "-DVulkan_INCLUDE_DIR=$(Join-Path $VulkanSdk 'Include')",
        "-DVulkan_LIBRARY=$(Join-Path $VulkanSdk 'Lib\vulkan-1.lib')",
        "-DVulkan_GLSLC_EXECUTABLE=$(Join-Path $VulkanSdk 'Bin\glslc.exe')"
    )
} else {
    Write-Error "GEMMA_BACKEND must be vulkan or cuda"
}
foreach ($item in $Def) { if ($item) { $cmakeArgs += "-D$item" } }
$quoted = foreach ($a in $cmakeArgs) { if ($a -match '\s') { '"' + $a + '"' } else { $a } }
cmd /c ('cmake ' + ($quoted -join ' '))
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "configure ok - backend $Backend"
