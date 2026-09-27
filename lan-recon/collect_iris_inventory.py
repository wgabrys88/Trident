"""Read-only LAN inventory for Iris (Windows). Writes iris-inventory.json."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_PATH = SCRIPT_DIR / "iris-inventory.json"


def run_ps(script: str) -> str:
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0 and r.stderr.strip():
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def ps_json(script: str):
    out = run_ps(script.rstrip() + " | ConvertTo-Json -Depth 8 -Compress")
    out = out.strip()
    if not out:
        return None
    return json.loads(out)


def normalize_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def collect_os() -> dict:
    data = ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption, Version, BuildNumber"
    )
    if isinstance(data, list):
        data = data[0]
    return {
        "caption": data.get("Caption", ""),
        "version": data.get("Version", ""),
        "build": str(data.get("BuildNumber", "")),
    }


def ensure_str_list(val) -> list:
    if val is None or val == {}:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None and x != {}]
    return [str(val)]


def collect_adapters() -> list:
    script = r"""
$configs = Get-NetIPConfiguration -ErrorAction SilentlyContinue
$adapters = Get-NetAdapter -ErrorAction SilentlyContinue
$result = @()
foreach ($a in $adapters) {
  $cfg = $configs | Where-Object { $_.InterfaceIndex -eq $a.ifIndex } | Select-Object -First 1
  $v4 = @()
  $v6 = @()
  $gw = @()
  $dns = @()
  $dhcp = $null
  if ($cfg) {
    foreach ($ip in ($cfg.IPv4Address | Where-Object { $_ })) { $v4 += $ip.IPAddress.ToString() }
    foreach ($ip in ($cfg.IPv6Address | Where-Object { $_ })) { $v6 += $ip.IPAddress.ToString() }
    foreach ($g in ($cfg.IPv4DefaultGateway | Where-Object { $_ })) { $gw += $g.NextHop.ToString() }
    foreach ($g in ($cfg.IPv6DefaultGateway | Where-Object { $_ })) { $gw += $g.NextHop.ToString() }
    foreach ($d in ($cfg.DNSServer | Where-Object { $_ })) {
      if ($d.ServerAddresses) { $dns += @($d.ServerAddresses) }
    }
    if ($cfg.NetIPv4Interface) {
      $dhcp = ($cfg.NetIPv4Interface.Dhcp -eq 'Enabled')
    }
  }
  $speedBps = $null
  $ls = $a.LinkSpeed
  if ($null -ne $ls) {
    if ($ls -is [uint64] -or $ls -is [int64] -or $ls -is [int]) {
      $speedBps = [int64]$ls
    } elseif ($ls -is [string]) {
      if ($ls -match '(?i)^([\d.]+)\s*Gbps') { $speedBps = [int64]([double]$matches[1] * 1000000000) }
      elseif ($ls -match '(?i)^([\d.]+)\s*Mbps') { $speedBps = [int64]([double]$matches[1] * 1000000) }
      elseif ($ls -match '(?i)^([\d.]+)\s*Kbps') { $speedBps = [int64]([double]$matches[1] * 1000) }
      elseif ($ls -match '(?i)^([\d.]+)\s*bps') { $speedBps = [int64][double]$matches[1] }
    }
  }
  $ifType = switch -Regex ($a.InterfaceDescription) {
    '(?i)wireless|wi-fi|wifi|802\.11' { 'wifi' }
    '(?i)tailscale' { 'tailscale' }
    '(?i)wireguard|wintun' { 'wireguard' }
    '(?i)virtual|hyper-v|vmware|vethernet|loopback|vpn|tap|tun' { 'virtual' }
    '(?i)ethernet|realtek|intel.*connection|gbe|2\.5g|i225|i219' { 'ethernet' }
    default {
      if ($a.MediaType -eq 'Native 802.11') { 'wifi' }
      elseif ($a.Name -match '(?i)wi-fi|wlan|wireless') { 'wifi' }
      elseif ($a.Name -match '(?i)ethernet') { 'ethernet' }
      else { 'other' }
    }
  }
  $result += [ordered]@{
    name = $a.Name
    description = $a.InterfaceDescription
    mac = ($a.MacAddress -replace '-', ':').ToLower()
    status = $a.Status.ToString()
    if_type = $ifType
    speed_bps = $speedBps
    ipv4 = $v4
    ipv6 = $v6
    dhcp_enabled = $dhcp
    gateway = @($gw | Select-Object -Unique)
    dns = @($dns | Select-Object -Unique)
  }
}
$result
"""
    raw = ps_json(script)
    adapters = normalize_list(raw)
    for a in adapters:
        for key in ("ipv4", "ipv6", "gateway", "dns"):
            a[key] = ensure_str_list(a.get(key))
    return adapters


def collect_routes_default() -> list:
    script = r"""
Get-NetRoute -DestinationPrefix '0.0.0.0/0','::/0' -ErrorAction SilentlyContinue |
  Sort-Object RouteMetric |
  Select-Object DestinationPrefix, NextHop, InterfaceAlias, InterfaceIndex, RouteMetric, AddressFamily
"""
    raw = ps_json(script)
    items = normalize_list(raw)
    out = []
    for r in items:
        out.append(
            {
                "destination": r.get("DestinationPrefix", ""),
                "next_hop": r.get("NextHop", ""),
                "interface": r.get("InterfaceAlias", ""),
                "interface_index": r.get("InterfaceIndex"),
                "metric": r.get("RouteMetric"),
                "address_family": r.get("AddressFamily"),
            }
        )
    return out


def collect_wifi() -> dict | None:
    script = r"""
$wlan = Get-NetAdapter -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '(?i)wi-fi|wlan|wireless' -or $_.InterfaceDescription -match '(?i)wireless|wi-fi|802\.11' } |
  Select-Object -First 1
if (-not $wlan) { return $null }
$profile = Get-NetConnectionProfile -InterfaceIndex $wlan.ifIndex -ErrorAction SilentlyContinue | Select-Object -First 1
$connected = ($wlan.Status -eq 'Up')
$ssid = ''
if ($profile) { $ssid = [string]$profile.Name }
$parsed = [ordered]@{
  connected = $connected
  ssid = $ssid
  bssid = ''
  signal = ''
  radio = ''
}
try {
  $raw = netsh wlan show interfaces 2>&1 | Out-String
  if ($raw -notmatch 'location permission|error 5') {
    foreach ($line in ($raw -split "`n")) {
      if ($line -match '^\s*BSSID\s*:\s*(.+)') { $parsed.bssid = ($matches[1].Trim() -replace '-', ':').ToLower() }
      if ($line -match '^\s*Signal\s*:\s*(.+)') { $parsed.signal = $matches[1].Trim() }
      if ($line -match '^\s*Radio type\s*:\s*(.+)') { $parsed.radio = $matches[1].Trim() }
      if ($line -match '^\s*SSID\s*:\s*(.+)') {
        $s = $matches[1].Trim()
        if ($s) { $parsed.ssid = $s }
      }
      if ($line -match '^\s*State\s*:\s*(.+)') {
        $parsed.connected = ($matches[1].Trim() -match '(?i)connected')
      }
    }
  }
} catch {}
if (-not $connected -and -not $parsed.ssid) { return $null }
$parsed
"""
    raw = ps_json(script)
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return raw


def collect_listening_ports_sample(limit: int = 30) -> list:
    script = rf"""
$conns = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object {{ $_.LocalAddress -notin @('127.0.0.1','::1','0.0.0.0') -and $_.LocalAddress -notlike '127.*' }} |
  Sort-Object LocalPort |
  Select-Object -First {limit} LocalAddress, LocalPort, OwningProcess
$out = @()
foreach ($c in $conns) {{
  $pname = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
  if (-not $pname) {{ $pname = "pid:$($c.OwningProcess)" }}
  $out += [ordered]@{{
    proto = 'tcp'
    local_address = $c.LocalAddress
    local_port = [int]$c.LocalPort
    process = $pname
  }}
}}
$out
"""
    raw = ps_json(script)
    return normalize_list(raw)


def collect_python_info() -> dict:
    exe = sys.executable
    return {
        "available": True,
        "version": platform.python_version(),
        "path": exe,
    }


def build_notes(adapters: list) -> list:
    notes = []
    notes.append(
        "netsh wlan show interfaces omitted BSSID/signal/radio: Windows location privacy blocks WLAN details for this session."
    )
    for a in adapters:
        desc = (a.get("description") or "").lower()
        name = (a.get("name") or "").lower()
        if "tailscale" in desc or "tailscale" in name:
            notes.append(f"Tailscale adapter detected: {a.get('name')}")
        if "wireguard" in desc or "wintun" in desc:
            notes.append(f"WireGuard-related adapter detected: {a.get('name')}")
        if a.get("if_type") == "wifi" and a.get("status") == "Up":
            notes.append(f"Primary Wi-Fi adapter candidate: {a.get('name')}")
        if a.get("if_type") == "ethernet" and a.get("status") == "Up":
            notes.append(f"Ethernet adapter candidate: {a.get('name')}")
    return notes


def main() -> int:
    hostname = run_ps("$env:COMPUTERNAME").strip()
    local_now = datetime.now().astimezone()
    collected_at = local_now.isoformat(timespec="seconds")

    adapters = collect_adapters()
    inventory = {
        "role": "iris",
        "hostname": hostname,
        "collected_at_local": collected_at,
        "os": collect_os(),
        "adapters": adapters,
        "routes_default": collect_routes_default(),
        "wifi": collect_wifi(),
        "listening_ports_sample": collect_listening_ports_sample(30),
        "python": collect_python_info(),
        "notes": build_notes(adapters),
    }

    OUT_PATH.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(inventory, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
