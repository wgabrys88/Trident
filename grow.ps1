$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$temporaryDirectory = Join-Path $runtimeDirectory 'temp'
New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
$env:TEMP = $temporaryDirectory
$env:TMP = $temporaryDirectory
$env:PIP_CACHE_DIR = Join-Path $runtimeDirectory 'pip-cache'
$pythonDirectory = Join-Path $runtimeDirectory 'python'
$python = Join-Path $pythonDirectory 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.11 -m venv $pythonDirectory
    if ($LASTEXITCODE) { throw 'Python 3.11 virtual environment creation failed' }
}
& $python -m pip install -r packages.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
$nativeDirectory = Join-Path $runtimeDirectory 'llama'
New-Item -ItemType Directory -Path $nativeDirectory -Force | Out-Null
foreach ($artifact in @('llama-b11435-bin-win-cuda-12.4-x64.zip','cudart-llama-bin-win-cuda-12.4-x64.zip')) {
    $archivePath = Join-Path $runtimeDirectory $artifact
    & curl.exe --fail --silent --show-error --location --max-time 300 "https://github.com/ggml-org/llama.cpp/releases/download/b11435/$artifact" --output $archivePath
    if ($LASTEXITCODE) { throw "Download failed: $artifact" }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $nativeDirectory -Force
}
$modelDirectory = Join-Path $runtimeDirectory 'models'
New-Item -ItemType Directory -Path $modelDirectory -Force | Out-Null
foreach ($modelName in @('LFM2.5-VL-3B-Q4_K_M.gguf','mmproj-LFM2.5-VL-3B-Q8_0.gguf')) {
    $modelPath = Join-Path $modelDirectory $modelName
    if (-not (Test-Path -LiteralPath $modelPath)) {
        & curl.exe --fail --silent --show-error --location --max-time 300 "https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF/resolve/6f730e9a2c454e8af9adc29db58e638e01e5957f/$modelName" --output $modelPath
        if ($LASTEXITCODE) { throw "Download failed: $modelName" }
    }
}
Write-Output 'Installed. Start with: .\runtime\python\Scripts\python.exe -B organism.py'
