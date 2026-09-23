# Copies CUDA MSBuild integration into VS Build Tools (requires admin).
$ErrorActionPreference = "Stop"
$src = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\visual_studio_integration\MSBuildExtensions"
$dst = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Microsoft\VC\v170\BuildCustomizations"
if (-not (Test-Path $src)) { Write-Error "missing $src" }
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dst -Force
Write-Host "CUDA 12.6 MSBuild integration installed to $dst"
