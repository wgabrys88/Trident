Describe 'restart-once.ps1' {
    BeforeAll {
        $scriptPath = Join-Path $PSScriptRoot 'restart-once.ps1'
    }

    It 'rejects a missing Trident root' {
        try { & $scriptPath -TridentRoot (Join-Path $TestDrive 'missing') -Engine luna } catch { $errorText = $_.Exception.Message }
        $errorText | Should Match 'root does not exist'
    }

    It 'rejects a missing entrypoint' {
        $root = Join-Path $TestDrive 'empty'
        New-Item -ItemType Directory -Path $root | Out-Null
        try { & $scriptPath -TridentRoot $root -Engine luna } catch { $errorText = $_.Exception.Message }
        $errorText | Should Match 'entrypoint does not exist'
    }

    It 'rejects a negative delay' {
        try { & $scriptPath -TridentRoot $PSScriptRoot -Engine luna -DelaySeconds -1 } catch { $errorText = $_.Exception.Message }
        $errorText | Should Match 'non-negative'
    }
}
