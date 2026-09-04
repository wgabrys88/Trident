$root = "C:\Users\eb-wjt\Downloads\Trident"
$env:PYTHONIOENCODING = "utf-8"
$step = 0

function Run-Brain($prompt) {
    $script:step++
    $tmp = New-TemporaryFile
    & python "$root\brain.py" --request $prompt 2>"$($tmp.FullName).err" | Set-Content -Encoding utf8 "$($tmp.FullName).out"
    $raw = Get-Content -Raw -Encoding utf8 "$($tmp.FullName).out"
    $after = $raw -replace '(?s)^.*?<channel\|>', ''
    $after = $after -replace '\s*\[ Prompt:.*$', ''
    $after = $after -replace '\s*Exiting\.\.\.\s*$', ''
    $clean = $after.Trim()
    Write-Host "[$($script:step) BRAIN] $clean"
    Remove-Item "$($tmp.FullName).out", "$($tmp.FullName).err" -Force -ErrorAction SilentlyContinue
    return $clean
}

function Run-TTS($text) {
    $script:step++
    $tmp = New-TemporaryFile
    & python "$root\tts_nano.py" --text $text 2>"$($tmp.FullName).err" | Set-Content -Encoding utf8 "$($tmp.FullName).out"
    $file = (Get-Content -Encoding utf8 "$($tmp.FullName).out" | Where-Object { $_.Trim() -ne '' } | Select-Object -Last 1).Trim()
    Write-Host "[$($script:step) TTS] $file"
    Remove-Item "$($tmp.FullName).out", "$($tmp.FullName).err" -Force -ErrorAction SilentlyContinue
    return $file
}

function Run-ASR($wav) {
    $script:step++
    $tmp = New-TemporaryFile
    & python "$root\parakeet.py" $wav 2>"$($tmp.FullName).err" | Set-Content -Encoding utf8 "$($tmp.FullName).out"
    $transcript = (Get-Content -Encoding utf8 "$($tmp.FullName).out" | Where-Object { $_.Trim() -ne '' }) -join ' '
    Write-Host "[$($script:step) ASR] $transcript"
    Remove-Item "$($tmp.FullName).out", "$($tmp.FullName).err" -Force -ErrorAction SilentlyContinue
    return $transcript.Trim()
}

$seed   = "generate in english numbers from 1 to 30 each one as a separate word delimited with dot, for example One. Two. Three. and so on"
$brain1 = Run-Brain $seed
$wav1   = Run-TTS   $brain1
$asr1   = Run-ASR   $wav1
$brain2 = Run-Brain "paraphrase: $asr1"
$wav2   = Run-TTS   $brain2
$asr2   = Run-ASR   $wav2

Write-Host ""
Write-Host "===== FINAL PIPELINE TRANSCRIPT ====="
Write-Host "seed -> brain -> tts -> asr -> brain -> tts -> asr"
Write-Host "  INPUT  : $seed"
Write-Host "  BRAIN1 : $brain1"
Write-Host "  WAV1   : $wav1"
Write-Host "  ASR1   : $asr1"
Write-Host "  BRAIN2 : $brain2"
Write-Host "  WAV2   : $wav2"
Write-Host "  ASR2   : $asr2"
