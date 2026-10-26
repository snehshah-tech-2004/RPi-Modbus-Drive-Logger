# RPi Modbus Drive Logger

Raspberry Pi data logger that polls Modbus RTU slaves over a serial (RS-485) port, writes timestamped readings to local CSV files, and uploads them to Google Drive as Google Sheets whenever Wi-Fi is available.

## Features
- Polls up to 8 Modbus slaves each cycle (holding registers, configurable address/count)
- Scales raw values by a division factor; out-of-range values are clamped to 0
- Slave timeout is logged as zeros so logging never stops
- Compensated sleep keeps the log interval accurate despite serial read time
- Per-slave daily CSV files, auto-uploaded every 30 s to a single Drive folder
- Offline-safe: keeps logging locally and resumes uploads when Wi-Fi returns

## Local file layout
```
Scanner_Files/
  DD-MM-YYYY/
    Slave_ID1/DD-MM-YYYY_ID_1.csv
    Slave_ID2/DD-MM-YYYY_ID_2.csv
```

## Setup
```
pip install pydrive2 pymodbus
```

1. Copy `calibration_settings.example.txt` to `calibration_settings.txt` and fill in your values.
2. Create a Google Cloud OAuth client and save it as `client_secrets.json`.
3. Run the script once on a machine with a browser to authorize Drive. This creates `credentials.json`.
4. Copy `client_secrets.json` and `credentials.json` to the Pi, next to the script. They are git-ignored; never commit them.
5. Start logging:
```
python3 Final_3_v3_Latest.py
```

## Settings (`calibration_settings.txt`)
| Key | Meaning |
|---|---|
| log_interval | Seconds between cycles |
| division_factor | Raw value divisor |
| port, baudrate, parity, stopbits, bytesize, timeout | Serial settings |
| register_address, register_count | Holding registers to read |
| slave_ids | Comma-separated slave IDs (max 8) |
| folder_id | Google Drive folder ID |
| clamp_min, clamp_max | Values outside this range are logged as 0 (defaults -250 / 20000) |

## Remote access (Tailscale)
```
sudo apt-get install apt-transport-https
echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian bookworm main" | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo rm -rf /var/lib/apt/lists/*
sudo mkdir -p /var/lib/apt/lists/partial
sudo apt-get clean
sudo apt-get update
sudo apt-get install tailscale
```
If that fails:
```
curl -fsSL https://tailscale.com/install.sh | sh
```
Then:
```
sudo tailscale up
```
Open the printed link and authenticate.
