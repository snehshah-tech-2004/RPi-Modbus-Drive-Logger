import os
import csv
import datetime
import time
import re
import threading
import socket

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pymodbus.client import ModbusSerialClient as ModbusClient


# ======================================================
# WIFI CHECK
# ======================================================
def wifi_available():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except:
        return False


# ======================================================
# GOOGLE DRIVE AUTH
# RPi-safe: no browser needed after first-time setup.
#
# FIRST-TIME SETUP (do this once on a PC or via SSH with
# port-forwarding, then copy credentials.json to RPi):
#   1. Run this script once on a machine with a browser.
#   2. It opens a browser → you approve → credentials.json is saved.
#   3. Copy credentials.json to the same folder as this script on RPi.
#   4. From then on, RPi uses saved credentials silently (no browser).
#
# If running headless from the start, set HEADLESS = True below.
# It will print a URL — open it on any device, paste the code back.
# ======================================================
HEADLESS = True   # Set False only if you have a desktop/browser on RPi

def authenticate_google_drive():
    try:
        gauth = GoogleAuth()

        # Force offline access and refresh token
        gauth.settings['client_config_file'] = 'client_secrets.json'
        gauth.settings['oauth_scope'] = ['https://www.googleapis.com/auth/drive']
        gauth.settings['get_refresh_token'] = True
        gauth.settings['oauth_flow_params'] = {
            'access_type': 'offline',
            'prompt': 'consent'
        }

        gauth.LoadCredentialsFile("credentials.json")

        if gauth.credentials is None:
            gauth.LocalWebserverAuth()
        elif gauth.access_token_expired:
            gauth.Refresh()
        else:
            gauth.Authorize()

        gauth.SaveCredentialsFile("credentials.json")
        return GoogleDrive(gauth)

    except Exception as e:
        print(f"Error during Google Drive authentication: {e}")
        return None

# ======================================================
# SETTINGS LOADER
# Supports # comments — full-line and inline
# ======================================================
def load_calibration_settings():
    settings = {}

    with open("calibration_settings.txt") as f:
        for line in f:
            line = line.split("#")[0].strip()   # strip inline comments
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            settings[k.strip()] = v.strip()

    # Parse and validate slave_ids
    raw_ids = settings.get("slave_ids", "1")
    slave_ids = [int(s.strip()) for s in raw_ids.split(",") if s.strip().isdigit()]
    if not slave_ids:
        raise ValueError("slave_ids is empty or invalid in calibration_settings.txt")
    if len(slave_ids) > 8:
        raise ValueError("slave_ids cannot have more than 8 entries")

    return {
        "log_interval":     int(settings["log_interval"]),
        "division_factor":  float(settings["division_factor"]),
        "port":             settings["port"],
        "baudrate":         int(settings["baudrate"]),
        "parity":           settings["parity"],
        "stopbits":         int(settings["stopbits"]),
        "bytesize":         int(settings["bytesize"]),
        "timeout":          float(settings["timeout"]),  # float so 0.5 is valid
        "register_address": int(settings["register_address"]),
        "register_count":   int(settings["register_count"]),
        "slave_ids":        slave_ids,
        "folder_id":        settings["folder_id"],
        "clamp_max":        float(settings.get("clamp_max", "20000")),
        "clamp_min":        float(settings.get("clamp_min", "-250")),
    }


# ======================================================
# DIRECTORY + CSV
# Structure (local only):
#   Scanner_Files/
#     DD-MM-YYYY/
#       Slave_ID1/
#         DD-MM-YYYY_ID_1.csv
#       Slave_ID2/
#         DD-MM-YYYY_ID_2.csv
# ======================================================
def create_slave_directory(today_date, slave_id):
    path = os.path.join("Scanner_Files", today_date, f"Slave_ID{slave_id}")
    os.makedirs(path, exist_ok=True)
    return path


def write_to_csv(directory_path, today_date, slave_id, data):
    filename = f"{today_date}_ID_{slave_id}.csv"
    csv_file_path = os.path.join(directory_path, filename)
    with open(csv_file_path, "a", newline="") as f:
        csv.writer(f).writerow(data)
    return csv_file_path


# ======================================================
# CSV CLEANER
# Validates: date, time, then <register_count> numeric values
# ======================================================
def clean_csv_file(input_file_path, register_count):
    cleaned_file_path = input_file_path.replace(".csv", "_cleaned.csv")
    valid_lines = []

    date_pattern = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")
    expected_cols = 2 + register_count

    with open(input_file_path, "r", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < expected_cols:
                continue
            if not (date_pattern.match(row[0]) and time_pattern.match(row[1])):
                continue
            try:
                for v in row[2:expected_cols]:
                    float(v)
            except (ValueError, TypeError):
                continue
            valid_lines.append(row[:expected_cols])

    with open(cleaned_file_path, "w", newline="") as f:
        csv.writer(f).writerows(valid_lines)

    return cleaned_file_path


# ======================================================
# GOOGLE DRIVE UPLOAD (UPDATED - SINGLE FOLDER)
# All files go directly into the main folder_id
# No subfolders created on Google Drive
# ======================================================
def upload_to_google_drive_as_sheet(drive, file_path, folder_id, register_count):
    try:
        cleaned = clean_csv_file(file_path, register_count)
        title = os.path.basename(file_path).replace(".csv", "")

        file_list = drive.ListFile({
            "q": f"'{folder_id}' in parents and trashed=false"
        }).GetList()

        existing = next((f for f in file_list if f["title"] == title), None)

        if existing:
            gfile = drive.CreateFile({"id": existing["id"]})
        else:
            gfile = drive.CreateFile({
                "title": title,
                "parents": [{"id": folder_id}],
                "mimeType": "text/csv"
            })

        gfile.SetContentFile(cleaned)
        gfile.Upload({"convert": True})

        if cleaned != file_path and os.path.exists(cleaned):
            os.remove(cleaned)

        print(f"☁️  Uploaded: {title}")

    except Exception as e:
        print(f"⚠️  Upload failed ({os.path.basename(file_path)}): {e}")


# ======================================================
# BACKGROUND UPLOAD THREAD — Simplified (Single Folder)
# One thread per slave, but all files go to the same Google Drive folder
# ======================================================
def upload_worker(get_csv_path_fn, folder_id, register_count):
    drive = None

    while True:
        time.sleep(30)

        csv_path = get_csv_path_fn()
        if not csv_path:
            continue

        if not wifi_available():
            drive = None
            continue

        if drive is None:
            drive = authenticate_google_drive()
            if drive is None:
                continue

        upload_to_google_drive_as_sheet(drive, csv_path, folder_id, register_count)


# ======================================================
# MODBUS — read one slave
# ======================================================
def read_modbus_slave(settings, slave_id):
    client = ModbusClient(
        port=settings["port"],
        baudrate=settings["baudrate"],
        parity=settings["parity"],
        stopbits=settings["stopbits"],
        bytesize=settings["bytesize"],
        timeout=settings["timeout"],
    )

    try:
        if not client.connect():
            print(f"⚠️  Cannot open port {settings['port']}")
            return None

        result = client.read_holding_registers(
            address=settings["register_address"],
            count=settings["register_count"],
            slave=slave_id,
        )

        if result.isError():
            return None

        return result.registers

    except Exception as e:
        print(f"⚠️  Modbus error (slave {slave_id}): {e}")
        return None

    finally:
        client.close()


# ======================================================
# MAIN LOGGING LOOP
# ======================================================
def start_logging():
    settings = load_calibration_settings()

    log_interval    = settings["log_interval"]
    division_factor = settings["division_factor"]
    slave_ids       = settings["slave_ids"]
    folder_id       = settings["folder_id"]
    register_count  = settings["register_count"]
    clamp_max       = settings["clamp_max"]
    clamp_min       = settings["clamp_min"]

    # Fixed start date — filenames don't change mid-run
    file_start_date = datetime.date.today().strftime("%d-%m-%Y")

    # Create per-slave directories up front (local only)
    slave_dirs      = {sid: create_slave_directory(file_start_date, sid) for sid in slave_ids}
    slave_csv_paths = {sid: None for sid in slave_ids}

    # Each thread gets a closure over its own slave_id
    def make_csv_getter(sid):
        def get_csv():
            return slave_csv_paths[sid]
        return get_csv

    # Start one upload thread per slave (Single Google Drive folder)
    for sid in slave_ids:
        threading.Thread(
            target=upload_worker,
            args=(make_csv_getter(sid), folder_id, register_count),
            daemon=True
        ).start()

    print(f"🟢 LOGGING STARTED — Slaves: {slave_ids} | All files uploading to single Google Drive folder")

    while True:
        # Stamp cycle start — used for compensated sleep at the bottom.
        # Without this, actual interval = log_interval + serial_work_time.
        # e.g. 4 slaves at 9600 baud adds ~200ms per cycle before sleep even starts.
        # A timed-out slave adds 2000ms (timeout value) on top of that.
        cycle_start = time.monotonic()

        date     = datetime.datetime.now().strftime("%d-%m-%Y")
        time_now = datetime.datetime.now().strftime("%H:%M:%S")

        for idx, sid in enumerate(slave_ids):
            raw = read_modbus_slave(settings, sid)

            if raw is None:
                # Slave failed → log zeros, keep running
                print(f"⚠️  Slave {sid} no response — logging zeros")
                values = [0.0] * register_count
            else:
                values = [
                    0.0 if (v > clamp_max or v < clamp_min) else round(v / division_factor, 4)
                    for v in raw
                ]

            row = [date, time_now] + values
            slave_csv_paths[sid] = write_to_csv(
                slave_dirs[sid], file_start_date, sid, row
            )

            print(f"📝 Slave {sid}: {values}")

            # 1ms turnaround delay between slaves
            if idx < len(slave_ids) - 1:
                time.sleep(0.001)

        # Compensated sleep: subtract time already spent this cycle.
        # max(0, ...) prevents negative sleep if work exceeded log_interval.
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, log_interval - elapsed)
        if elapsed > log_interval:
            print(f"⚠️  Cycle took {elapsed:.3f}s > log_interval {log_interval}s — reduce timeout or slave count")
        time.sleep(sleep_for)


# ======================================================
# START
# ======================================================
if __name__ == "__main__":
    start_logging()
