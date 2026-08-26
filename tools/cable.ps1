param(
    [Parameter(Mandatory = $true)][ValidateSet('list', 'default', 'set')][string]$Action,
    [string]$EndpointId
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace TridentAudio
{
    public enum ERole : uint { eConsole = 0, eMultimedia = 1 }

    [Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPolicyConfig
    {
        [PreserveSig] int GetMixFormat();
        [PreserveSig] int GetDeviceFormat();
        [PreserveSig] int ResetDeviceFormat();
        [PreserveSig] int SetDeviceFormat();
        [PreserveSig] int GetProcessingPeriod();
        [PreserveSig] int SetProcessingPeriod();
        [PreserveSig] int GetShareMode();
        [PreserveSig] int SetShareMode();
        [PreserveSig] int GetPropertyValue();
        [PreserveSig] int SetPropertyValue();
        [PreserveSig] int SetDefaultEndpoint([In, MarshalAs(UnmanagedType.LPWStr)] string endpoint, ERole role);
        [PreserveSig] int SetEndpointVisibility();
    }

    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    internal class CPolicyConfigClient { }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDeviceEnumerator
    {
        int EnumAudioEndpoints();
        int GetDefaultAudioEndpoint(uint dataFlow, uint role, [MarshalAs(UnmanagedType.IUnknown)] out object device);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDevice
    {
        int Activate();
        int OpenPropertyStore();
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        int GetState();
    }

    public static class Cable
    {
        public static string DefaultCaptureId()
        {
            var enumerator = (IMMDeviceEnumerator)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")));
            object unknown;
            enumerator.GetDefaultAudioEndpoint(1, 0, out unknown);
            var device = (IMMDevice)unknown;
            string id;
            device.GetId(out id);
            return id;
        }

        public static void SetDefault(string endpointId)
        {
            var client = (IPolicyConfig)(object)new CPolicyConfigClient();
            foreach (ERole role in new[] { ERole.eConsole, ERole.eMultimedia })
            {
                int hr = client.SetDefaultEndpoint(endpointId, role);
                Marshal.ThrowExceptionForHR(hr);
            }
        }
    }
}
"@

switch ($Action) {
    'list' {
        $root = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'
        foreach ($key in Get-ChildItem $root) {
            $guid = $key.PSChildName
            $state = (Get-ItemProperty $key.PSPath -Name DeviceState -ErrorAction SilentlyContinue).DeviceState
            if ($state -ne 1) { continue }
            $props = Get-ItemProperty ($key.PSPath + '\Properties') -ErrorAction SilentlyContinue
            $name = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
            if ($name) { Write-Output ("{0}|{1}" -f "{0.0.1.00000000}.$guid", $name) }
        }
    }
    'default' {
        Write-Output ([TridentAudio.Cable]::DefaultCaptureId())
    }
    'set' {
        if (-not $EndpointId) { throw "set requires -EndpointId" }
        [TridentAudio.Cable]::SetDefault($EndpointId)
        Write-Output 'ok'
    }
}
