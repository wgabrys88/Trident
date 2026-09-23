# Bare-metal Chatterbox (Vulkan): one process, one sentence — no brain, no pipe server.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("nano", "turbo", "v3")]
    [string]$Variant,
    [string]$Text = "Direct Vulkan test.",
    [string]$Language = "en",
    [string]$Out = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root "build\bin\chatterbox.exe"
if (-not (Test-Path $exe)) { throw "build first: cmake --build build --config Release --target chatterbox" }
$args = @($Variant, "-t", $Text, "-l", $Language)
if ($Out) { $args += @("-o", $Out) }
& $exe @args
