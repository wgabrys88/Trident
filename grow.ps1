$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $env:LOCALAPPDATA 'Python\bin\python.exe'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 -m pip install -r packages.txt
    $run = 'py -3.11 -B organism.py'
} elseif (Test-Path -LiteralPath $python) {
    & $python -m pip install -r packages.txt
    $run = "& '$python' -B organism.py"
} else {
    throw 'Python 3.11 is required'
}
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
$nativeDirectory = Join-Path $PSScriptRoot 'runtime\llama'
New-Item -ItemType Directory -Path $nativeDirectory -Force | Out-Null
$server = Join-Path $nativeDirectory 'llama-server.exe'
if (-not (Test-Path -LiteralPath $server)) {
    $artifact = 'llama-b11435-bin-win-vulkan-x64.zip'
    $archivePath = Join-Path $PSScriptRoot "runtime\$artifact"
    & curl.exe --fail --silent --show-error --location "https://github.com/ggml-org/llama.cpp/releases/download/b11435/$artifact" --output $archivePath
    if ($LASTEXITCODE) { throw "Download failed: $artifact" }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $nativeDirectory -Force
}
$modelDirectory = Join-Path $env:LOCALAPPDATA 'Trident\models'
New-Item -ItemType Directory -Path $modelDirectory -Force | Out-Null
foreach ($modelName in @('LFM2.5-VL-3B-Q4_K_M.gguf','mmproj-LFM2.5-VL-3B-Q8_0.gguf')) {
    $modelPath = Join-Path $modelDirectory $modelName
    if (-not (Test-Path -LiteralPath $modelPath)) {
        & curl.exe --fail --silent --show-error --location "https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/6f730e9a2c454e8af9adc29db58e638e01e5957f/$modelName" --output $modelPath
        if ($LASTEXITCODE) { throw "Download failed: $modelName" }
    }
}
Write-Output "Installed. Start with: $run"
