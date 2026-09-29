$ErrorActionPreference = "Stop"
$isaCode = @"
using System;
using System.Runtime.InteropServices;
public static class GemmaCpuIsa {
    [DllImport("kernel32.dll")]
    static extern bool IsProcessorFeaturePresent(uint feature);
    public static string Detect() {
        if (IsProcessorFeaturePresent(41)) return "AVX512";
        if (IsProcessorFeaturePresent(40)) return "AVX2";
        if (IsProcessorFeaturePresent(39)) return "AVX";
        return "baseline";
    }
}
"@
Add-Type -TypeDefinition $isaCode -Language CSharp
$isa = [GemmaCpuIsa]::Detect()
$arch = switch ($isa) {
    "AVX512" { "/arch:AVX512" }
    "AVX2"   { "/arch:AVX2" }
    "AVX"    { "/arch:AVX" }
    default  { "" }
}
Write-Output "msvc_arch=$arch"
Write-Output "host_isa=$isa"
