"""
ota_update.py  –  GitHub OTA updater for MicroPython on Raspberry Pi Pico W
============================================================================
Fetches a manifest from a GitHub repo and replaces local files that have
changed.  Designed to be triggered from an MQTT message handler.

SETUP
-----
1. Host a file called `manifest.json` at the root of your GitHub repo
   (or the branch/path you configure below).  Example manifest:

   {
       "version": "1.4.2",
       "files": [
           {"path": "main.py",          "sha256": "abc123..."},
           {"path": "lib/umqtt.py",     "sha256": "def456..."},
           {"path": "lib/sensor.py",    "sha256": "789abc..."}
       ]
   }

2. Populate the CONFIG block below.

3. Call `ota_update.check_and_update()` from your MQTT callback, e.g.:

   import ota_update

   def mqtt_callback(topic, msg):
       if topic == b"device/update":
           result = ota_update.check_and_update()
           if result["updated"]:
               machine.reset()          # reboot to apply changes

NOTES
-----
* Files are downloaded to a .tmp file first; the rename only happens after
  the SHA-256 digest is verified, so a bad download never clobbers your
  working copy.
* Directories inside /lib (or any sub-path) are created automatically.
* The function returns a result dict so you can log/publish the outcome over
  MQTT before optionally rebooting.
* Requires urequests and uhashlib – both are available in standard
  MicroPython firmware for the Pico W.
"""

import gc
import os
import json
import time
import uhashlib
import ubinascii
import urequests

# ── CONFIG ──────────────────────────────────────────────────────────────────

GITHUB_USER   = "daviddupont1959-sys"
GITHUB_REPO   = "JMD_1000-pico-firmware"
GITHUB_BRANCH = "main"

# Path inside the repo where manifest.json lives (no leading slash).
# Use "" for the repo root.
REPO_BASE_PATH = "https://github.com/daviddupont1959-sys/JMD_1000-pico-firmware"

# Root on the Pico's filesystem where your project lives.
# "/" means the root; change to "/project" etc. if needed.
LOCAL_ROOT = "/"

# Raw-content base URL (no trailing slash).
RAW_BASE = (
    f"https://raw.githubusercontent.com"
    f"/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
)

# Request timeout in seconds.
TIMEOUT = 15

# ── INTERNAL HELPERS ────────────────────────────────────────────────────────

def _log(msg):
    """Simple timestamped print; replace with your own logger if preferred."""
    print(f"[OTA] {msg}")


def _full_url(repo_path):
    """Build a raw GitHub URL for a file path relative to the repo root."""
    if REPO_BASE_PATH:
        return f"{RAW_BASE}/{REPO_BASE_PATH}/{repo_path}"
    return f"{RAW_BASE}/{repo_path}"


def _local_path(repo_path):
    """Map a repo-relative path to an absolute path on the Pico."""
    root = LOCAL_ROOT.rstrip("/")
    return f"{root}/{repo_path}"


def _ensure_dirs(filepath):
    """Create all parent directories for *filepath* if they don't exist."""
    parts = filepath.split("/")
    # parts[0] is "" for absolute paths; skip it
    accumulated = ""
    for part in parts[:-1]:          # everything except the filename
        if not part:
            continue
        accumulated = f"{accumulated}/{part}"
        try:
            os.mkdir(accumulated)
        except OSError:
            pass                     # already exists


def _sha256_of_file(path):
    """Return the hex SHA-256 digest of a local file, or None if missing."""
    try:
        h = uhashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                h.update(chunk)
        return ubinascii.hexlify(h.digest()).decode()
    except OSError:
        return None


def _sha256_of_bytes(data: bytes) -> str:
    h = uhashlib.sha256(data)
    return ubinascii.hexlify(h.digest()).decode()


def _fetch_json(url):
    """GET *url* and return parsed JSON, or raise on error."""
    _log(f"GET {url}")
    r = urequests.get(url, timeout=TIMEOUT)
    try:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} fetching {url}")
        return r.json()
    finally:
        r.close()
        gc.collect()


def _fetch_bytes(url):
    """GET *url* and return raw bytes, or raise on error."""
    _log(f"GET {url}")
    r = urequests.get(url, timeout=TIMEOUT)
    try:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} fetching {url}")
        return r.content
    finally:
        r.close()
        gc.collect()


# ── PUBLIC API ───────────────────────────────────────────────────────────────

def check_and_update(force=False):
    """
    Fetch the remote manifest and update any files whose SHA-256 has changed.

    Parameters
    ----------
    force : bool
        If True, re-download and overwrite all files listed in the manifest
        regardless of whether their digest matches.

    Returns
    -------
    dict with keys:
        updated  (bool)  – True if at least one file was replaced
        version  (str)   – version string from the remote manifest
        changed  (list)  – repo-relative paths that were updated
        skipped  (list)  – paths that were already up to date
        errors   (list)  – paths that failed (original file preserved)
    """
    result = {
        "updated": False,
        "version": None,
        "changed": [],
        "skipped": [],
        "errors":  [],
    }

    # ── 1. Fetch manifest ────────────────────────────────────────────────────
    try:
        manifest_url = _full_url("manifest.json")
        manifest = _fetch_json(manifest_url)
    except Exception as exc:
        _log(f"Failed to fetch manifest: {exc}")
        result["errors"].append(f"manifest: {exc}")
        return result

    result["version"] = manifest.get("version", "unknown")
    _log(f"Remote version: {result['version']}")

    # ── 2. Process each file ─────────────────────────────────────────────────
    for entry in manifest.get("files", []):
        repo_path     = entry["path"]          # e.g. "lib/sensor.py"
        remote_sha    = entry.get("sha256", "")
        local_path    = _local_path(repo_path)
        tmp_path      = local_path + ".tmp"

        # Skip if local copy is already current
        if not force:
            local_sha = _sha256_of_file(local_path)
            if local_sha and local_sha == remote_sha:
                _log(f"  OK (unchanged): {repo_path}")
                result["skipped"].append(repo_path)
                continue

        # Download
        try:
            data = _fetch_bytes(_full_url(repo_path))
        except Exception as exc:
            _log(f"  FAIL download {repo_path}: {exc}")
            result["errors"].append(repo_path)
            continue

        # Verify digest (skip check if manifest omits sha256)
        if remote_sha:
            actual_sha = _sha256_of_bytes(data)
            if actual_sha != remote_sha:
                _log(
                    f"  FAIL digest mismatch {repo_path}: "
                    f"expected {remote_sha}, got {actual_sha}"
                )
                result["errors"].append(repo_path)
                del data
                gc.collect()
                continue

        # Write to .tmp, then atomically rename
        try:
            _ensure_dirs(local_path)
            with open(tmp_path, "wb") as f:
                f.write(data)
            del data
            gc.collect()

            # Remove original before rename (MicroPython lacks os.replace)
            try:
                os.remove(local_path)
            except OSError:
                pass
            os.rename(tmp_path, local_path)

            _log(f"  UPDATED: {repo_path}")
            result["changed"].append(repo_path)
            result["updated"] = True

        except Exception as exc:
            _log(f"  FAIL write {repo_path}: {exc}")
            # Clean up orphaned .tmp if present
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            result["errors"].append(repo_path)

        # Small pause between downloads to keep the Wi-Fi stack happy
        time.sleep_ms(100)

    _log(
        f"Done. updated={len(result['changed'])} "
        f"skipped={len(result['skipped'])} "
        f"errors={len(result['errors'])}"
    )
    return result
