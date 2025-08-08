import requests
import json

# === User Config === #
REQUEST_TIMEOUT = 10
# =  = = = = = = = = #

with open("config.json") as f:
    config = json.load(f)
    REAL_DEBRID_API_TOKEN = config["REAL_DEBRID_API_TOKEN"]

def format_speed(bytes_per_sec):
    if bytes_per_sec is None:
        return "0.00MB/s"
    mbps = bytes_per_sec / (1024 * 1024)
    return f"{mbps:.2f}MB/s"

def format_size(bytes_size):
    if bytes_size is None:
        return "0.00GB"
    gb = bytes_size / (1024 ** 3)
    return f"{gb:.2f}GB"

def fetch_in_progress_torrents(limit=250):
    headers = {"Authorization": f"Bearer {REAL_DEBRID_API_TOKEN}"}
    try:
        resp = requests.get(
            "https://api.real-debrid.com/rest/1.0/torrents",
            params={"limit": limit},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        torrents = resp.json()
    except requests.RequestException as e:
        print(f"⚠️ Error fetching torrents: {e}")
        return []

    # Filter to keep only torrents NOT completed (status != downloaded)
    in_progress = [t for t in torrents if t.get("status") != "downloaded"]
    return in_progress


def formated_torrents_data(torrents):
    for t in torrents:
        progress = t.get("progress", 0.0)
        speed = t.get("speed", 0)
        filename = t.get("filename", "Unnamed")
        size = t.get("bytes", 0)

        print(f"{progress:.2f}% @ {format_speed(speed)} - {filename} ({format_size(size)})")

if __name__ == "__main__":

    limit_input = input("Enter number of recent torrents to fetch (default 250): ").strip()
    limit = int(limit_input) if limit_input.isdigit() and int(limit_input) > 0 else 250
    
    print(f"Checking last {limit} torrents, in progress will be shown below\n")

    torrents = fetch_in_progress_torrents(limit)
    if torrents:
        formated_torrents_data(torrents)
    else:
        print("\nNo in-progress torrents found.")
