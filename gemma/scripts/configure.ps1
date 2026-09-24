param(
    [string]$Backend = "vulkan",
    [string]$VulkanSdk = "",
    [string]$CudaRoot = "",
    [string]$Toolset = "cuda=12.6",
    [string]$Generator = "Visual Studio 17 2022",
    [string]$Arch = "x64",
    [string[]]$Def = @()
)
$ErrorActionPreference = "Stop"
$gemmaRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot "detect_cpu.ps1")

$build = Join-Path $gemmaRoot "build"
$cmakeArgs = @(
    "-S", $gemmaRoot,
    "-B", $build,
    "-G", $Generator,
    "-A", $Arch,
    "-DCMAKE_BUILD_TYPE=Release",
    "-DGEMMA_BACKEND=$Backend"
)
if ($Backend -eq "cuda") {
    if (-not $CudaRoot) { $CudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6" }
    $nvcc = Join-Path $CudaRoot "bin\nvcc.exe"
    if (-not (Test-Path $nvcc)) { Write-Error "CUDA nvcc not found at $nvcc" }
    $banner = & $nvcc --version | Out-String
    if ($banner -match "release (\d+)\.(\d+)" -and ([int]$Matches[1] -gt 12 -or ([int]$Matches[1] -eq 12 -and [int]$Matches[2] -ge 7))) {
        Write-Error "CUDA $($Matches[1]).$($Matches[2]) does not compile for Pascal. Use CUDA 12.6."
    }
    $env:CUDA_PATH = $CudaRoot
    $cmakeArgs += @(
        "-T", $Toolset,
        "-DCMAKE_CUDA_COMPILER=$nvcc",
        "-DCMAKE_CUDA_TOOLKIT_ROOT_DIR=$CudaRoot"
    )
} else {
    if (-not $VulkanSdk) {
        if ($env:VULKAN_SDK -and (Test-Path (Join-Path $env:VULKAN_SDK "Bin\glslc.exe"))) {
            $VulkanSdk = $env:VULKAN_SDK
        } else {
            $hit = Get-ChildItem "C:\VulkanSDK\*\Bin\glslc.exe" | Sort-Object { [version]$_.Directory.Parent.Name } | Select-Object -Last 1
            if (-not $hit) { Write-Error "Vulkan SDK not found" }
            $VulkanSdk = $hit.Directory.Parent.FullName
        }
    }
    $env:VULKAN_SDK = $VulkanSdk
    $cmakeArgs += @(
        "-DCMAKE_PREFIX_PATH=$VulkanSdk",
        "-DVulkan_INCLUDE_DIR=$(Join-Path $VulkanSdk 'Include')",
        "-DVulkan_LIBRARY=$(Join-Path $VulkanSdk 'Lib\vulkan-1.lib')",
        "-DVulkan_GLSLC_EXECUTABLE=$(Join-Path $VulkanSdk 'Bin\glslc.exe')"
    )
}
foreach ($item in $Def) { if ($item) { $cmakeArgs += "-D$item" } }
$quoted = foreach ($a in $cmakeArgs) { if ($a -match '\s') { '"' + $a + '"' } else { $a } }
cmd /c ('cmake ' + ($quoted -join ' '))
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "configure ok - backend $Backend"
