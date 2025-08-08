import requests
import json


# === User Config === #
REQUEST_TIMEOUT = 10
PAGE_LIMIT = 500  # Max torrents per page
# =  = = = = = = = = #

with open("config.json") as f:
    config = json.load(f)
    REAL_DEBRID_API_TOKEN = config["REAL_DEBRID_API_TOKEN"]


def fetch_torrents_page(page=1, limit=PAGE_LIMIT):
    headers = {"Authorization": f"Bearer {REAL_DEBRID_API_TOKEN}"}
    try:
        resp = requests.get(
            "https://api.real-debrid.com/rest/1.0/torrents",
            params={"page": page, "limit": limit},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        total_count = int(resp.headers.get("X-Total-Count", "0"))
        return resp.json(), total_count
    except requests.RequestException as e:
        print(f"⚠️ Error fetching torrents page {page}: {e}")
        return [], 0

def fetch_all_torrents():
    all_torrents = []
    page = 1
    total_count = None

    print("🔄 Retrieving all torrents...")

    while True:
        torrents, total_count = fetch_torrents_page(page=page, limit=PAGE_LIMIT)
        if not torrents:
            break
        all_torrents.extend(torrents)
        print(f" {len(torrents)} torrents found on page {page} (total found: {len(all_torrents)})")

        # Calculate total pages
        total_pages = (total_count + PAGE_LIMIT - 1) // PAGE_LIMIT if total_count else 0

        if page >= total_pages:
            break
        page += 1

    print(f"✅ Found {len(all_torrents)} torrents in total.")
    return all_torrents

def delete_torrent(torrent_id):
    headers = {"Authorization": f"Bearer {REAL_DEBRID_API_TOKEN}"}
    try:
        resp = requests.delete(
            f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 204:
            print(f"🗑️ Deleted torrent ID: {torrent_id}")
            return True
        else:
            print(f"⚠️ Failed to delete torrent ID {torrent_id}: Status {resp.status_code}")
            return False
    except requests.RequestException as e:
        print(f"⚠️ Exception deleting torrent ID {torrent_id}: {e}")
        return False

def find_and_remove_duplicates():
    torrents = fetch_all_torrents()
    if not torrents:
        print("No torrents fetched.")
        return

    # Sort by oldest first
    torrents.sort(key=lambda t: t.get("generated", 0))

    hash_groups = {}
    for t in torrents:
        thash = t.get("hash")
        if not thash:
            continue
        hash_groups.setdefault(thash, []).append(t)

    duplicates = []

    print("\n🔍 Checking for duplicate torrents...\n")

    for thash, group in hash_groups.items():
        if len(group) > 1:
            print(f"Hash: {thash}")
            for i, torrent in enumerate(group):
                tid = torrent.get("id")
                name = torrent.get("filename", "Unnamed")
                if i == 0:
                    print(f"  ✔️  Kept:    {name} (ID: {tid})")
                else:
                    print(f"  🗑️  Duplicate: {name} (ID: {tid})")
                    duplicates.append((tid, name, thash))
            print()

    if not duplicates:
        print("✅ No duplicates found.")
        return

    confirm = input("⚠️ Proceed with deleting these duplicates? (y/N): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("❌ Deletion cancelled.")
        return

    print("\nDeleting duplicates...\n")

    for tid, name, thash in duplicates:
        delete_torrent(tid)

if __name__ == "__main__":
    try:
        find_and_remove_duplicates()
    except KeyboardInterrupt:
        print("\nAborted by user.")
