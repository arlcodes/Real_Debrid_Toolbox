import os
import time
import json
import shutil
import requests
import webbrowser
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ==================== #
#     User Config      #
# ==================== #

API_BASE = "https://api.real-debrid.com/rest/1.0"
REQUEST_TIMEOUT = 10 # timeout for all API calls
MAX_RETRIES = 3 # retries for file processes
PAGE_LIMIT = 250 # RD Torrents per 'page' - max 500 recommended by RD
DOWNLOAD_DIR = "Downloaded Files"
PROCESSED_DIR = "Processed Files"
UNRESTRICT_RETRIES = 2 # number of attempts to generate download links
UNRESTRICT_DELAY = 5  # seconds between retries
MAX_PARALLEL_DOWNLOADS = 4 # max 4 recommended by RD
CHECK_PREMIUM = True # start with a account check

# === Load API Key === #
try:
    with open("config.json") as f:
        config = json.load(f)
        TOKEN = config["REAL_DEBRID_API_TOKEN"]
except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
    print(f"❌ Failed to load config.json: {e}")
    exit(1)

# ---------------------------
# Utilities
# ---------------------------

def rd_request(method, endpoint, **kwargs):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    resp = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()

def retry_request(func, retries=MAX_RETRIES, delay=2, *args, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except requests.RequestException as e:
            print(f"⚠️ Attempt {attempt} failed: {e}")
            if attempt < retries:
                print(f"⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
    return None

def prompt_limit(prompt="Type how many torrents to check, or leave blank for all (default): "):
    while True:
        choice = input(prompt).strip()
        if choice == "":
            return None
        if choice.isdigit() and int(choice) > 0:
            return int(choice)
        print("❌ Invalid input. Please enter a positive number or leave blank for all.")

def check_user_info():
    try:
        resp = requests.get(
            f"{API_BASE}/user",
            headers={"Authorization": f"Bearer {TOKEN}"},
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


# ---------------------------
# Shared Helpers
# ---------------------------


def format_size(num):
    if num is None:
        return "unknown size"
    try:
        b = int(num)
    except Exception:
        return "unknown size"
    if b >= 1 << 30:
        return f"{b / (1 << 30):.2f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.2f} MB"
    if b >= 1 << 10:
        return f"{b / (1 << 10):.2f} KB"
    return f"{b} B"

def format_speed(bytes_per_sec):
    if bytes_per_sec is None:
        return "0.00MB/s"
    mbps = bytes_per_sec / (1024 * 1024)
    return f"{mbps:.2f}MB/s"

def resolve_filename_collision(directory, filename):
    base, ext = os.path.splitext(filename)
    candidate = filename
    i = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{base} ({i}){ext}"
        i += 1
    return candidate

def open_url(url):
    try:
        webbrowser.open(url)
        print("✅ URL opened in your default browser.")
    except Exception as e:
        print(f"⚠️ Failed to open browser: {e}")

def parse_selection(selection_input, max_index):
    selection = set()
    invalid_parts = []

    for part in selection_input.split(","):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = map(int, part.split('-'))
                if 1 <= start <= end <= max_index:
                    selection.update(range(start, end + 1))
                else:
                    invalid_parts.append(part)
            except ValueError:
                invalid_parts.append(part)
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= max_index:
                selection.add(idx)
            else:
                invalid_parts.append(part)
        else:
            invalid_parts.append(part)

    if invalid_parts:
        print(f"⚠️ Ignored invalid parts: {', '.join(invalid_parts)}")

    return sorted(selection)

def delete_torrent(tid):
    try:
        resp = requests.delete(f"{API_BASE}/torrents/delete/{tid}",
                               headers={"Authorization": f"Bearer {TOKEN}"},
                               timeout=REQUEST_TIMEOUT)
        if resp.status_code == 204:
            print(f"🗑️ Deleted torrent ID: {tid}")
            return True
        print(f"⚠️ Failed to delete torrent ID {tid}: Status {resp.status_code}")
    except requests.RequestException as e:
        print(f"⚠️ Exception deleting torrent ID {tid}: {e}")
    return False

def fetch_torrents(limit=None, filter=None):
    all_torrents = []
    page = 1
    headers = {"Authorization": f"Bearer {TOKEN}"}

    print(f"🔄 Retrieving {'last ' + str(limit) if limit else 'all'} torrents...\n")

    while True:
        try:
            resp = requests.get(
                f"{API_BASE}/torrents",
                params={"page": page, "limit": PAGE_LIMIT},
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            torrents = resp.json()
            total_count = int(resp.headers.get("X-Total-Count", "0"))
        except requests.RequestException as e:
            print(f"⚠️ Error fetching torrents page {page}: {e}")
            break

        if not torrents:
            break

        all_torrents.extend(torrents)

        # stop if we've reached the user-defined limit
        if limit is not None and len(all_torrents) >= limit:
            all_torrents = all_torrents[:limit]
            break

        # stop if we've fetched all pages
        total_pages = (total_count + PAGE_LIMIT - 1) // PAGE_LIMIT if total_count else 0
        if page >= total_pages:
            break
        
        print(f"📄 Found {len(torrents)} torrents on page {page}")

        page += 1

    if filter == 'inprogress':
        filtered = [t for t in all_torrents if t.get("status") != "downloaded"]
        print(f"✅ Checked {len(all_torrents)} torrents — found {len(filtered)} in-progress.\n")
        return filtered

    print(f"✅ Found {len(all_torrents)} torrents in total.\n")
    return all_torrents


# ---------------------------
# Downloader Function
# ---------------------------
def torrent_downloader():
    # 1 How many torrents to fetch
    while True:
        choice = input("Type how many torrents to check, or leave blank for default (100): ").strip().lower()
        if choice == "":
            limit = 100  # last 100 default
            break
        elif choice.isdigit() and int(choice) > 0:
            limit = int(choice)
            break
        else:
            print("❌ Invalid input. Please enter a positive number or leave blank for all.")

    # 2 Skip non-downloaded?
    
    while True:
        choice = input("Skip non-downloaded torrents? [Y/n] (default Y): ").strip().lower()

        if choice in ("", "y", "yes"):
            skip_non_downloaded = True
            print("Skipping non-completed torrents")
            break
        elif choice in ("n", "no"):
            skip_non_downloaded = False
            print("Non-completed torrents will be shown with status, download links can't be generated for these files")
            break
        else:
            print("❌ Invalid input. Please select Y, y, N, or n.")


    # 3 Fetch torrents
    torrents = fetch_torrents(limit=limit)
    if not torrents:
        print("No torrents found in your Real-Debrid account.")
        return

    # 4 Filter out non completed torrents
    if skip_non_downloaded:
        torrents = [t for t in torrents if t.get("status") == "downloaded"]

    if not torrents:
        print("No eligible torrents found.")
        return

    # 5 Show avaliable torrents
    print("\nAvailable torrents:\n")
    for idx, t in enumerate(torrents, 1):
        size_str = format_size(t.get("bytes"))
        status = t.get("status", "").lower()
        if status == "downloaded":
            print(f"{idx}: {t.get('filename')} ({size_str})")
        else:
            print(f"{idx}: {t.get('filename')} ({size_str}) - Status: {status}")

    # 6 Select torrents (supports ranges and multiple selections)
    sel = input("\nSelect torrents by number (comma separated, ranges allowed, or 0 for all): ").strip()
    if sel == "0" or sel == "":
        selected = torrents
    else:
        indices = parse_selection(sel, len(torrents))
        selected = [torrents[i - 1] for i in indices]

    if not selected:
        print("⚠️ No torrents selected.")
        return

    # 7 Generate download links
    all_files = []
    file_map = []  # keep track of files with numbering
    for t in selected:
        print(f"\nGenerating download links for {t['filename']}...")
        files = unrestrict_torrent_links(t['id'])
        if not files:
            print("⚠️ No files found or failed to generate links.")
            continue
        for f in files:
            file_map.append(f)
            print(f"{len(file_map)} - {f['filename']} ({format_size(f['filesize'])})")
        all_files.extend(files)

    if not all_files:
        print("⚠️ No unrestricted links could be generated.")
        return

    # 8 File selection
    sel_files = input("\nSelect files to download (comma separated, ranges allowed, or 0 for all): ").strip()
    if sel_files == "0" or sel_files == "":
        selected_files = file_map
    else:
        indices = parse_selection(sel_files, len(file_map))
        selected_files = [file_map[i-1] for i in indices]

    # 9 Parallel download prompt
    if len(selected_files) > 1:
        parallel_choice = input("Download files in parallel? (y/N): ").strip().lower()
        if parallel_choice == "y":
            while True:
                try:
                    num_workers = int(input(f"How many files to download at once? (1-{min(MAX_PARALLEL_DOWNLOADS,len(selected_files))}): "))
                    if 1 <= num_workers <= min(MAX_PARALLEL_DOWNLOADS,len(selected_files)):
                        break
                except ValueError:
                    continue

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = []
                for pos, f in enumerate(selected_files):
                    futures.append(executor.submit(download_file, f["download"], f["filename"], pos))
                for future in as_completed(futures):
                    future.result()
            return

    # 10 Run downloads
    for f in selected_files:
        download_file(f["download"], f["filename"])

def unrestrict_torrent_links(torrent_id, max_retries=UNRESTRICT_RETRIES, delay=UNRESTRICT_DELAY):
    """
    Process to generate download links, catches HTTP errors with retries.
    """
    try:
        info = rd_request("GET", f"torrents/info/{torrent_id}")
    except requests.RequestException as e:
        print(f"⚠️ Failed to fetch torrent info for ID {torrent_id}: {e}")
        return []

    if "links" not in info or not info["links"]:
        return []

    unrestricted = []
    for raw_link in info["links"]:
        for attempt in range(1, max_retries + 1):
            try:
                result = rd_request("POST", "unrestrict/link", data={"link": raw_link})
                unrestricted.append({
                    "filename": result.get("filename"),
                    "download": result.get("download"),
                    "filesize": result.get("filesize"),
                })
                break  # success, move to next link
            except requests.RequestException as e:
                print(f"⚠️ Attempt {attempt} failed to unrestrict link: {e}")
                if attempt < max_retries:
                    print(f"⏳ Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    print("❌ Skipping this link due to repeated failures.")
    return unrestricted

def download_file(url, filename, position=0):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    filename = resolve_filename_collision(DOWNLOAD_DIR, filename)
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    
    try:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as resp:
                    resp.raise_for_status()
                    total_size = int(resp.headers.get("Content-Length", 0))
                    chunk_size = 8192
                    with open(filepath, "wb") as f, tqdm(
                        total=total_size,
                        unit='B',
                        unit_scale=True,
                        desc=filename,
                        position=position,
                        leave=True,
                        dynamic_ncols=True,
                        bar_format="{percentage:3.0f}% {n_fmt}/{total_fmt} @ {rate_fmt} - {desc}"
                    ) as pbar:
                        for chunk in resp.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)
                                pbar.update(len(chunk))
                print(f"✅ Downloaded: {filename}")
                return True
            except requests.RequestException as e:
                print(f"\n⚠️ Download attempt {attempt} failed for '{filename}': {e}")
                time.sleep(2)
        print(f"\n❌ Failed to download: {filename}")
        return False
    except KeyboardInterrupt:
        print("\n⚠️ Download interrupted by user.")
    return False



# ---------------------------
# Hosted Links Converter
# ---------------------------

def hoster_downloader():
#Handles direct hosted links added to links.txt, basic catching for single file or folders 

    # 1. Optional host status check
    check_status = input("Check host status first? [y/N] (default N): ").strip().lower()
    if check_status in ("y", "yes"):
        try:
            status = rd_request("GET", "hosts/status")
            
            only_online = input("Only show online hosts? [Y/n] (default Y): ").strip().lower()
            show_only_online = only_online in ("", "y", "yes")
            
            print("\n=== Host Status ===")
            for host, info in status.items():
                is_online = info.get("supported") and info.get("status") == "up"
                
                if show_only_online and not is_online:
                    continue  # skip offline hosts
                
                line = f"{host:20} - "
                if is_online:
                    line += "✅ Online"
                else:
                    line += "❌ Offline/Unsupported"
                print(line)
            print()
            
        except requests.RequestException as e:
            print(f"⚠️ Failed to fetch host status: {e}")

    # 2. Load links.txt file (case-insensitive)
    links_file = None
    for f in os.listdir():
        if f.lower() == "links.txt":
            links_file = f
            break

    if not links_file:
        print("❌ No 'links.txt' file found in current directory.")
        return

    with open(links_file, "r", encoding="utf-8") as f:
        raw_links = [line.strip() for line in f if line.strip()]

    if not raw_links:
        print("❌ 'links.txt' file is empty.")
        return

    print(f"\n📄 Found {len(raw_links)} links in '{links_file}'.\n")

    # 3. Unrestrict each link
    unrestricted = []
    for link in raw_links:
        try:
            print(f"\n🔄 Processing {link}\n")
            processed = False

            # 3a. Basic check if link is a folder, (mega has folder in URL, not sure about other hosts)
            if "folder" in link.lower():
                try:
                    result = rd_request("POST", "unrestrict/folder", data={"link": link})
                    if result:
                        for file_info in result:
                            if isinstance(file_info, dict):
                                filename = file_info.get("filename", "Unknown")
                                download = file_info.get("download")
                                filesize = file_info.get("filesize")
                                # Try to unrestrict if download is missing
                                if not download and "link" in file_info:
                                    try:
                                        dl = rd_request("POST", "unrestrict/link", data={"link": file_info["link"]})
                                        download = dl.get("download")
                                        filesize = dl.get("filesize", filesize)
                                        filename = dl.get("filename", filename)
                                    except requests.RequestException:
                                        print(f"⚠️ Failed to unrestrict individual file: {filename}")
                            elif isinstance(file_info, str):
                                filename = file_info
                                download = None
                                filesize = None
                                # Try to unrestrict the string as a link
                                try:
                                    dl = rd_request("POST", "unrestrict/link", data={"link": file_info})
                                    filename = dl.get("filename", filename)
                                    download = dl.get("download")
                                    filesize = dl.get("filesize", filesize)
                                except requests.RequestException:
                                    print(f"⚠️ Failed to unrestrict file: {filename}")
                            else:
                                continue

                            unrestricted.append({
                                "filename": filename,
                                "download": download,
                                "filesize": filesize,
                            })

                            size_str = f" ({format_size(filesize)})" if filesize else ""
                            print(f"✅ Folder file: {filename}{size_str}")

                        processed = True
                    else:
                        print(f"⚠️ No files found in folder: {link}")
                        processed = True
                except requests.RequestException:
                    pass  # fallback to single-file if folder fails

            # 3b. Single-file unrestrict if not processed
            if not processed:
                try:
                    result = rd_request("POST", "unrestrict/link", data={"link": link})
                    filename = result.get("filename", link)
                    download = result.get("download")
                    filesize = result.get("filesize")
                    unrestricted.append({
                        "filename": filename,
                        "download": download,
                        "filesize": filesize,
                    })
                    size_str = f" ({format_size(filesize)})" if filesize else ""
                    print(f"✅ Processed: {filename}{size_str}")
                    processed = True
                except requests.RequestException:
                    # fallback to folder method
                    try:
                        result = rd_request("POST", "unrestrict/folder", data={"link": link})
                        if result:
                            for file_info in result:
                                if isinstance(file_info, dict):
                                    filename = file_info.get("filename", "Unknown")
                                    download = file_info.get("download")
                                    filesize = file_info.get("filesize")
                                    if not download and "link" in file_info:
                                        try:
                                            dl = rd_request("POST", "unrestrict/link", data={"link": file_info["link"]})
                                            download = dl.get("download")
                                            filesize = dl.get("filesize", filesize)
                                            filename = dl.get("filename", filename)
                                        except requests.RequestException:
                                            print(f"⚠️ Failed to unrestrict individual file: {filename}")
                                elif isinstance(file_info, str):
                                    filename = file_info
                                    download = None
                                    filesize = None
                                    try:
                                        dl = rd_request("POST", "unrestrict/link", data={"link": file_info})
                                        filename = dl.get("filename", filename)
                                        download = dl.get("download")
                                        filesize = dl.get("filesize", filesize)
                                    except requests.RequestException:
                                        print(f"⚠️ Failed to unrestrict file: {filename}")
                                else:
                                    continue

                                unrestricted.append({
                                    "filename": filename,
                                    "download": download,
                                    "filesize": filesize,
                                })
                                size_str = f" ({format_size(filesize)})" if filesize else ""
                                print(f"✅ Folder file (fallback): {filename}{size_str}")

                            processed = True
                        else:
                            print(f"❌ Could not process link: {link}")
                    except requests.RequestException:
                        print(f"❌ Could not process link at all: {link}")

        except Exception as e:
            print(f"⚠️ Unexpected error processing {link}: {e}")

    if not unrestricted:
        print("❌ No valid unrestricted links were generated.")
        return

    # 4. Let user pick files to download
    print("\nAvailable files:\n")
    for idx, f in enumerate(unrestricted, 1):
        size_str = f" ({format_size(f['filesize'])})" if f['filesize'] else ""
        print(f"{idx}: {f['filename']}{size_str}")
    print("\n0: Download all files")

    sel = input("\nSelect files by number (comma separated, ranges allowed, or 0 for all): ").strip()
    if sel in ("", "0"):
        selected = unrestricted
    else:
        indices = parse_selection(sel, len(unrestricted))
        selected = [unrestricted[i - 1] for i in indices]

    if not selected:
        print("⚠️ No files selected.")
        return

    # 5. Filter out files with no download URL
    selected_valid = []
    for f in selected:
        if f.get("download"):
            selected_valid.append(f)
        else:
            print(f"⚠️ Skipping '{f['filename']}' — no download URL available.")

    if not selected_valid:
        print("❌ None of the selected files have valid download URLs.")
        return

    # 6. Parallel downloads
    if len(selected_valid) > 1:
        parallel_choice = input("Download files in parallel? (y/N): ").strip().lower()
        if parallel_choice == "y":
            while True:
                try:
                    num_workers = int(input(f"How many files to download at once? (1-{min(MAX_PARALLEL_DOWNLOADS,len(selected_valid))}): "))
                    if 1 <= num_workers <= min(MAX_PARALLEL_DOWNLOADS, len(selected_valid)):
                        break
                except ValueError:
                    continue

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = []
                for pos, f in enumerate(selected_valid):
                    futures.append(executor.submit(download_file, f["download"], f["filename"], pos))
                for future in as_completed(futures):
                    future.result()
            return

    # 7. Sequential download
    for f in selected_valid:
        download_file(f["download"], f["filename"])


# ---------------------------
# In Progress Function
# ---------------------------
def in_progress():
    limit = prompt_limit("\nType how many torrents to check, or leave blank for all (default): ")
    torrents = fetch_torrents(limit=limit, filter='inprogress')

    if torrents:
        in_progress_checker(torrents)
    else:
        if limit == 0:
            print("\nNo Active Torrents\n")
        else:
            print(f"Checked {limit} torrents and found {len(torrents)} active")


def in_progress_checker(torrents):
    for t in torrents:
        progress = t.get("progress", 0.0)
        speed = t.get("speed", 0)
        filename = t.get("filename", "Unnamed")
        size = t.get("bytes", 0)
        status = t.get("status", "unknown").lower()
        print(
            f"{progress:.2f}% @ {format_speed(speed)} - {filename} "
            f"({format_size(size)}) - Status: {status}"
        )

# ---------------------------
# .torrent Uploader
# ---------------------------

def upload_torrents():
    torrents = list_torrent_files()
    if not torrents:
        print("No .torrent files found in current directory.")
        return

    print("\nSelect a .torrent file:\n")
    for idx, f in enumerate(torrents, 1):
        print(f"{idx}: {f}")
    print("\n0: All files\n")

    sel = input("Select torrents by number (comma separated, ranges allowed, or 0 for all): ").strip()
    if sel == "0" or sel == "":
        selected_indices = list(range(1, len(torrents) + 1))
    else:
        selected_indices = parse_selection(sel, len(torrents))

    if not selected_indices:
        print("⚠️ No torrents selected.")
        return

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    for idx in selected_indices:
        selected_file = torrents[idx - 1]
        print(f"\nProcessing {selected_file}")
        if process_torrent_workflow(selected_file, TOKEN):
            shutil.move(selected_file, os.path.join(PROCESSED_DIR, selected_file))

def list_torrent_files():
    return [f for f in os.listdir() if f.lower().endswith(".torrent")]

def upload_torrent_file(torrent_path, token, host=None):
    url = "https://api.real-debrid.com/rest/1.0/torrents/addTorrent"
    if host:
        url += f"?host={host}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
    with open(torrent_path, "rb") as f:
        data = f.read()
    try:
        resp = requests.put(url, headers=headers, data=data, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        if resp.status_code == 201:
            tid = resp.json().get("id")
            print(f"✅ Torrent uploaded successfully. ID: {tid}")
            return tid
        else:
            print(f"❌ Unexpected response: {resp.status_code} {resp.text}")
            return None
    except requests.RequestException as e:
        print(f"❌ Upload failed: {e}")
        return None

def get_torrent_info(torrent_id, token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}",
                            headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"⚠️ Error fetching torrent info: {e}")
        return None

def select_files(torrent_id, token, file_ids):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.post(
            f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}",
            data={"files": file_ids},
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code in (202, 204):
            print("✅ File selection sent successfully.")
            return True
        else:
            print(f"❌ Failed to select files: {resp.status_code} {resp.text}")
            return False
    except requests.RequestException as e:
        print(f"⚠️ Exception selecting files: {e}")
        return False

def wait_for_metadata(torrent_id, token, max_wait=15, interval=5):
    waited = 0
    while waited < max_wait:
        info = get_torrent_info(torrent_id, token)
        if not info:
            break
        status = info.get("status", "")
        files = info.get("files", [])
        print(f"⏳ Status: {status}")
        if status in ("waiting_files_selection", "waiting_files") and files:
            print(f"✅ Found {len(files)} files")
            return files, status
        elif status in ("downloading", "downloaded", "ready") and files:
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
        if selection_input == "":
            # User pressed Enter without typing numbers → return empty list
            return []
        selected_indexes = parse_selection(selection_input, len(files))
        if selected_indexes:
            return [files[i - 1]["id"] for i in selected_indexes]
        print("❌ Invalid selection. Try again.")

def wait_for_download_start(torrent_id, token, max_attempts=12, interval=5):
    attempts = 0
    while attempts < max_attempts:
        info = get_torrent_info(torrent_id, token)
        if not info:
            break
        status = info.get("status", "")
        if status in ("downloading", "downloaded", "ready"):
            print(f"✅ Success: Torrent status is {status}")
            return True
        print(f"⏳ Waiting for download to start... (status: {status})")
        time.sleep(interval)
        attempts += 1
    print("⚠️ Torrent did not start downloading in time.")
    return False

def process_torrent_workflow(torrent_path, token, host=None):
    attempt = 1
    tid = None  # Track TID so we can delete if no files selected
    while attempt <= MAX_RETRIES + 1:
        if attempt > 1:
            print(f"\n🔁 Retrying upload (Attempt {attempt} of {MAX_RETRIES + 1})")

        tid = upload_torrent_file(torrent_path, token, host)
        if not tid:
            attempt += 1
            continue

        files, status = wait_for_metadata(tid, token)
        if not files:
            delete_torrent(tid)
            attempt += 1
            continue

        if len(files) == 1:
            selected_ids = [files[0]["id"]]
            filename = files[0].get("path", files[0].get("filename", "Unknown"))
            print(f"✅ Automatically selecting single file: {filename}")
        else:
            selected_ids = prompt_user_to_select_files(files)

        # Check if user selected no files
        if not selected_ids:
            print(f"⚠️ No files selected for torrent ID {tid}, deleting torrent...")
            delete_torrent(tid)
            return False

        file_ids = ",".join(map(str, selected_ids))
        if not select_files(tid, token, file_ids):
            delete_torrent(tid)
            attempt += 1
            continue

        wait_for_download_start(tid, token)
        return True

    print("❌ Unable to process .torrent after multiple attempts. Please try manually.")
    return False



# ---------------------------
# Duplicate Remover
# ---------------------------

def remove_duplicates():
    # Prompt user for number of torrents with validation
    while True:
        choice = input("\nType how many torrents to check for duplicates, or leave blank for all (default): ").strip()
        if choice == "":
            limit = None  # all torrents
            break
        elif choice.isdigit() and int(choice) > 0:
            limit = int(choice)
            break
        else:
            print("❌ Invalid input. Please enter a positive number or leave blank for all.")

    # Fetch torrents
    torrents = fetch_torrents(limit=limit)

    if not torrents:
        print("No torrents fetched.")
        return

    print(f"\nChecked {len(torrents)} torrents for duplicates.\n")

    # Sort by generation timestamp
    torrents.sort(key=lambda t: t.get("generated", 0))

    # Group by hash to find duplicates
    hash_groups = {}
    for t in torrents:
        thash = t.get("hash")
        if not thash:
            continue
        hash_groups.setdefault(thash, []).append(t)

    duplicates = []

    print("🔍 Checking for duplicate torrents...\n")
    for thash, group in hash_groups.items():
        if len(group) > 1:
            print(f"Hash: {thash}")
            for i, torrent in enumerate(group):
                tid = torrent.get("id")
                name = torrent.get("filename", "Unnamed")
                if i == 0:
                    print(f"  ✅  Kept:    {name} (ID: {tid})")
                else:
                    print(f"  🗑️  Duplicate: {name} (ID: {tid})")
                    duplicates.append(tid)
            print()

    if not duplicates:
        print("✅ No duplicates found.")
        return

    confirm = input("⚠️ Proceed with deleting these duplicates? (y/N): ").strip().lower()
    if confirm in ("y", "yes"):
        for tid in duplicates:
            delete_torrent(tid)
        print(f"✅ Deleted {len(duplicates)} duplicates.")
    else:
        print("❌ Deletion cancelled.")


# ---------------------------
# Main Run
# ---------------------------
def main_menu():
       
    print("\nIf you liked this tool you can support via my referral code next time you renew your sub.\n")
    
    if CHECK_PREMIUM:
        check_user_info()
    
    while True:
        print("\n=== Real-Debrid Toolkit ===")

        print("1. Upload .torrent files")
        print("2. Check in-progress torrents")
        print("3. Download torrent files")
        print("4. Download hoster links")
        print("5. Find & remove duplicate torrents")
        print("6. Renew Subscription")


        print("0. Exit\n")
        choice = input("Select an option: ").strip()
        if choice == "1":
            upload_torrents()
        elif choice == "2":
            in_progress()
        elif choice == "3":
            torrent_downloader()
        elif choice == "4":
            hoster_downloader()
        elif choice == "5":  
            remove_duplicates()
        elif choice == "6":
            referal()

        elif choice == "0":
            url = "http://real-debrid.com/?id=3488563"
            print("If you liked this tool you can support via my referral code next time you renew your sub.")
            
            print(url)

            open_choice = input("Open URL now? (y/N): ").strip().lower()
            if open_choice == "y":
                try:
                    webbrowser.open(url)
                    print("✅ URL opened in your default browser.")
                except Exception as e:
                    print(f"⚠️ Failed to open browser: {e}")
            exit(0)
        else:
            print("Invalid selection. Try again.")

def referal():
    url = "http://real-debrid.com/?id=3488563"
    try:
        webbrowser.open(url)
        print("✅ URL opened in your default browser.")
    except Exception as e:
        print(f"⚠️ Failed to open browser: {e}")
    exit(0)

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        exit(1)
