import os
import shutil
import sys
import time
import requests
import json
from datetime import datetime, timezone

# === User Config === #
MAX_ATTEMPTS = 3
MAX_RETRIES = 2
REQUEST_TIMEOUT = 10
# ==================== #

# === Load Config === #
with open("config.json") as f:
    config = json.load(f)
    REAL_DEBRID_API_TOKEN = config["REAL_DEBRID_API_TOKEN"]

# === Helpers === #
def check_user_info():
    try:
        resp = requests.get(
            "https://api.real-debrid.com/rest/1.0/user",
            headers={"Authorization": f"Bearer {REAL_DEBRID_API_TOKEN}"},
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code == 200:
            user = resp.json()
            status = "✅ Premium" if user.get("premium") else "❌ Non-premium"
            expiration = user.get("expiration")
            if expiration:
                try:
                    exp_date = datetime.strptime(expiration, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                    days_left = (exp_date - datetime.now(timezone.utc)).days
                    print(f"{status} — expires in {days_left} day(s)")
                except ValueError:
                    print(f"{status} — expiration date format error: {expiration}")
            else:
                print(f"{status}")
        else:
            print("⚠️ Failed to retrieve user info.")
    except requests.RequestException as e:
        print(f"⚠️ Error getting user info: {e}")

def parse_selection(selection_str, max_index):
    result = set()
    parts = selection_str.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            try:
                start, end = map(int, part.split("-", 1))
                if start <= end:
                    for i in range(start, end + 1):
                        if 1 <= i <= max_index:
                            result.add(i)
            except Exception:
                pass
        else:
            try:
                val = int(part)
                if 1 <= val <= max_index:
                    result.add(val)
            except Exception:
                pass
    return sorted(result)

def list_torrent_files():
    return [f for f in os.listdir() if f.lower().endswith(".torrent")]

def _delete_torrent(torrent_id, headers):
    try:
        resp = requests.delete(
            f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code == 204:
            print(f"🗑️ Deleted torrent {torrent_id} from Real-Debrid.")
        else:
            print(f"⚠️ Failed to delete torrent {torrent_id}: {resp.status_code} {resp.text}")
    except requests.RequestException as e:
        print(f"⚠️ Exception deleting torrent {torrent_id}: {e}")

# === Torrent Workflow Functions === #
def upload_torrent_file(torrent_path, headers, host=None):
    url = "https://api.real-debrid.com/rest/1.0/torrents/addTorrent"
    if host:
        url += f"?host={host}"
    with open(torrent_path, "rb") as f:
        data = f.read()
    try:
        resp = requests.put(url, headers={**headers, "Content-Type": "application/octet-stream"}, data=data, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        if resp.status_code == 201:
            torrent_id = resp.json().get("id")
            print(f"✅ Torrent uploaded successfully. ID: {torrent_id}")
            return torrent_id
        else:
            print(f"❌ Unexpected response: {resp.status_code} {resp.text}")
            return None
    except requests.RequestException as e:
        print(f"❌ Upload failed: {e}")
        return None

def get_torrent_info(torrent_id, headers):
    try:
        resp = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}", headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"⚠️ Error fetching torrent info: {e}")
        return None

def select_files(torrent_id, headers, file_ids):
    try:
        resp = requests.post(
            f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}",
            data={"files": file_ids},
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code in (204, 202):
            print("✅ File selection sent successfully.")
            return True
        else:
            print(f"❌ Failed to select files: {resp.status_code} {resp.text}")
            return False
    except requests.RequestException as e:
        print(f"⚠️ Exception selecting files: {e}")
        return False

def wait_for_download_start(torrent_id, headers):
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        info = get_torrent_info(torrent_id, headers)
        if not info:
            break
        status = info.get("status", "")
        if status in ("downloading", "downloaded", "ready"):
            print(f"✅ Success: (Status: {status})")
            return True
        print(f"⏳ Waiting for download to start... (status: {status})")
        attempts += 1
        time.sleep(5)
    return False

def wait_for_metadata(torrent_id, headers):
    waited = 0
    max_wait = 15
    interval = 5
    while waited < max_wait:
        info = get_torrent_info(torrent_id, headers)
        if not info:
            break
        status = info.get("status", "")
        files = info.get("files", [])
        print(f"⏳ Status: {status}")
        if status in ("waiting_files_selection", "waiting_files") and files:
            print(f"✅ Found {len(files)} files")
            return files, status
        elif status in ("downloading", "downloaded", "ready"):
            print(f"✅ Torrent is already downloading or ready (status: {status})")
            return files, status
        time.sleep(interval)
        waited += interval
    print("⚠️ Timeout waiting for metadata or files.")
    return None, None

def prompt_user_to_select_files(files):
    print("\nAvailable files in torrent:\n")
    for idx, f in enumerate(files, 1):
        name = f.get("path", f.get("filename", f.get("id")))
        size = f.get("bytes", 0)
        size_mb = round(size / (1024 * 1024), 2)
        print(f"{idx}: {name} ({size_mb} MB)")
    print("\n0: Select all files")
    while True:
        selection_input = input("\nEnter file numbers to select (e.g. 1,3-5) or 0 for all: ").strip()
        if selection_input == "0":
            return [f["id"] for f in files]
        selected_indexes = parse_selection(selection_input, len(files))
        if selected_indexes:
            return [files[i - 1]["id"] for i in selected_indexes]
        print("❌ Invalid selection. Try again.")

def process_torrent_workflow(torrent_path, token, host=None):
    headers = {"Authorization": f"Bearer {token}"}
    attempt = 1

    while attempt <= MAX_RETRIES + 1:
        if attempt > 1:
            print(f"\n🔁 Retrying upload (Attempt {attempt} of {MAX_RETRIES + 1})")

        torrent_id = upload_torrent_file(torrent_path, headers, host)
        if not torrent_id:
            attempt += 1
            continue

        files, status = wait_for_metadata(torrent_id, headers)
        if not files:
            _delete_torrent(torrent_id, headers)
            attempt += 1
            continue

        # Automatically select file if only one is present
        if len(files) == 1:
            selected_ids = [files[0]["id"]]
            filename = files[0].get("path", files[0].get("filename", "Unknown"))
            print(f"✅ Automatically selecting single file: {filename}")
        else:
            selected_ids = prompt_user_to_select_files(files)

        file_ids = ",".join(map(str, selected_ids))
        if not select_files(torrent_id, headers, file_ids):
            _delete_torrent(torrent_id, headers)
            attempt += 1
            continue

        wait_for_download_start(torrent_id, headers)
        return True

    print("❌ Unable to process .torrent after multiple attempts. Please try manually.")
    return False




# === Main === #
def main():
    print("\n=== Real-Debrid .torrent Uploader ===\n")
    check_user_info()
    print("\n")
    torrents = list_torrent_files()
    if not torrents:
        print("No .torrent files found in current directory.\n")
        return
    print("\nSelect a .torrent file:\n")
    for idx, f in enumerate(torrents, 1):
        print(f"{idx}: {f}")
    print("\n0: All files\n")
    while True:
        choice = input("Select torrent(s) to process: ").strip()
        if choice.isdigit():
            choice = int(choice)
            if choice == 0:
                selected_files = torrents
                break
            elif 1 <= choice <= len(torrents):
                selected_files = [torrents[choice - 1]]
                break
        print("Invalid choice. Try again.")
    for selected_file in selected_files:
        print(f"\nProcessing '{selected_file}'")
        success = process_torrent_workflow(selected_file, REAL_DEBRID_API_TOKEN)
        if success:
            processed_dir = os.path.join(os.getcwd(), "Processed Files")
            os.makedirs(processed_dir, exist_ok=True)
            shutil.move(selected_file, os.path.join(processed_dir, os.path.basename(selected_file)))

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
