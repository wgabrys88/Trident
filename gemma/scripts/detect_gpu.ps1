$ErrorActionPreference = "Stop"
$nvidia = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" })
$nvcc = @(
    $(if (Get-Command nvcc -ErrorAction SilentlyContinue) { (Get-Command nvcc).Source }),
    $(if ($env:CUDA_PATH) { Join-Path $env:CUDA_PATH "bin\nvcc.exe" }),
    "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin\nvcc.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($nvidia.Count -gt 0 -and $nvcc) { "cuda" } else { "vulkan" }
