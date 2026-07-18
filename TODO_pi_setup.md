# Pi Retro-Net Setup Script

Automated setup script for a Raspberry Pi that creates an isolated retro-gaming
WiFi subnet for older devices (PSP, legacy smartphones, DS, etc).

## Goal

One-command setup: `sudo ./pi_retro_net.sh` that turns a Pi into a dedicated
retro-device access point running psp-proxy (and potentially other services).

## Network Topologies

### Option A: Ethernet uplink + onboard WiFi AP (recommended)
- Pi connects to home router via ethernet (full-speed uplink)
- Onboard WiFi broadcasts a WPA-PSK (TKIP) network for retro devices
- Most reliable, no channel contention

### Option B: WiFi STA + WiFi AP (same radio, no ethernet)
- `wlan0` connects to home WiFi as a client (STA mode)
- Virtual `ap0` interface broadcasts the retro AP
- Both locked to the same channel (follows the home router)
- Lower throughput, can be flaky if home router channel-hops
- Uses `iw dev wlan0 interface add ap0 type __ap`

### Option C: WiFi STA + USB WiFi dongle AP
- `wlan0` connects to home WiFi
- USB dongle (`wlan1`) runs the AP on an independent channel
- Best wireless-only option — two real radios, no contention
- Needs a dongle with hostapd/AP-mode support (RT5370, RTL8188 chips are good)

## Script Responsibilities

### 1. Detect hardware & choose topology
- Check for ethernet link (`/sys/class/net/eth0/carrier`)
- Enumerate WiFi interfaces, detect USB dongle
- Auto-select best topology, allow override via flag

### 2. Install packages
```
apt install hostapd dnsmasq iptables-persistent
```

### 3. Configure hostapd (AP)
Key settings for PSP compatibility:
```ini
interface=<ap_interface>      # wlan0 (eth uplink) or ap0/wlan1
ssid=RetroNet
channel=6                     # PSP only supports 2.4 GHz, ch 1-11
hw_mode=g                     # 802.11g (PSP max)
wpa=1                         # WPA1 only — PSP cannot do WPA2
wpa_passphrase=<user-set>
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP             # PSP does NOT support AES/CCMP
```
- Note: WEP also works but WPA-PSK/TKIP is the best the PSP supports

### 4. Configure dnsmasq (DHCP + DNS)
```ini
interface=<ap_interface>
dhcp-range=192.168.4.10,192.168.4.50,24h
address=/#/192.168.4.1        # optional: captive-portal style redirect to proxy
```

### 5. Configure networking
- Static IP on AP interface (`192.168.4.1/24`)
- Enable IP forwarding (`net.ipv4.ip_forward=1`)
- NAT/masquerade from retro subnet to uplink
- Persist with `iptables-persistent` or nftables

### 6. Install & enable psp-proxy as a systemd service
```ini
[Unit]
Description=PSP Proxy
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/env uv run /opt/psp-proxy/psp_proxy.py --host 192.168.4.1
Restart=always
User=psp

[Install]
WantedBy=multi-user.target
```

### 7. Optional: captive portal redirect
- dnsmasq wildcard DNS points all domains to `192.168.4.1`
- iptables redirects port 80 traffic to psp-proxy on 8080
- PSP opens browser -> instant landing page, zero config

## Target Devices & Compatibility Notes

| Device             | WiFi           | Notes                              |
|--------------------|----------------|------------------------------------|
| PSP 1000/2000/3000 | WPA-PSK (TKIP) | No WPA2, 2.4 GHz only, 802.11b/g  |
| PSP Go             | WPA-PSK (TKIP) | Same as above                      |
| DS / DS Lite       | WEP only       | Need WEP fallback or separate SSID |
| 3DS                | WPA2 OK        | Works on modern networks too       |
| Old smartphones    | WPA-PSK (TKIP) | Android 2.x / iOS 3-5 era         |

## File Structure (planned)
```
pi_retro_net/
  setup.sh              # main installer
  config/
    hostapd.conf.tmpl    # template, script fills in SSID/pass/interface
    dnsmasq.conf.tmpl
    psp-proxy.service
  README.md
```

## Open Questions
- [ ] Should we support WEP fallback for DS Lite? (separate SSID or mixed mode?)
- [ ] Add mDNS/Avahi so `retro.local` resolves on modern devices for admin?
- [ ] Include a simple web admin panel for the Pi (view connected devices, logs)?
- [ ] Bundle other retro services? (e.g. FTP for PSP file transfers, SMB)
