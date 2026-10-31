"""
diag.py - Diagnostics helpers shared by every module (new in 3.10).

  log()            Crash-proof wrapper around state.log_file. Logging must never
                   be the reason the detector stops, so every failure is swallowed.
  log_limited()    Same, but writes at most once per interval per key. Use it for
                   anything that can repeat (network errors, retries) so the small
                   rotating log files are not flooded.
  run-state        A tiny JSON file remembering when motion was last seen, so a
                   restart does not forget how long the house has been quiet.
  crash record     Written by main.py when Detect.py dies, reported by Detect.py
                   (log + push notification) on the next boot.

Log levels used by the firmware (lowest to highest):
  debug  - hourly heartbeat, clock syncs, other routine diagnostics
  info   - boot summary, WiFi/MQTT restored, awake window opened/closed
  alert  - alert triggered / cleared / notification delivered
  error  - faults: WiFi or MQTT lost, notification failed, crash, bad config
"""
import os
import time
try:
    import ujson as json
except ImportError:
    import json
import state

RUNSTATE_FILE = "runstate.json"
CRASH_FILE = "crash.json"

_limited = {}


def log(level, msg):
    """Write msg to the log file at the given level ('debug','info','alert','error')."""
    try:
        print("[" + level.upper() + "] " + msg)
        lf = state.log_file
        if lf is None:
            return
        fn = getattr(lf, level, None)
        if fn is None:
            fn = lf.info
        fn(msg)
    except Exception as e:
        print("diag.log failed:", e)


def log_limited(key, level, msg, every_s=3600):
    """Like log(), but at most once per every_s seconds for this key.
    Returns True if the message was written."""
    try:
        now = time.ticks_ms()
        last = _limited.get(key)
        if last is not None and time.ticks_diff(now, last) < every_s * 1000:
            return False
        _limited[key] = now
    except Exception:
        pass
    log(level, msg)
    return True


def fmt_duration(seconds):
    """Seconds -> short human text, e.g. '3h05m', '12m30s', '45s'."""
    s = int(seconds)
    if s < 0:
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, s % 60)
    return "%ds" % s


# ----------------------------------------------------------------------
# Small JSON files in flash. Note: never name these *.tmp - main.py treats
# *.tmp files as pending OTA updates and renames them at boot.
# ----------------------------------------------------------------------
def _read_json(name):
    try:
        with open(name, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_json(name, obj):
    try:
        with open(name, "w") as f:
            f.write(json.dumps(obj))
        try:
            os.sync()
        except Exception:
            pass
        return True
    except Exception as e:
        print("diag: could not write", name, e)
        return False


def load_runstate():
    return _read_json(RUNSTATE_FILE)


def save_runstate(d):
    return _write_json(RUNSTATE_FILE, d)


def take_crash_message():
    """Return (message, crash_count) and blank the stored message so it is
    reported only once. The count is kept until the firmware has run stably."""
    c = _read_json(CRASH_FILE)
    msg = c.get("msg", "")
    n = c.get("n", 0)
    if msg:
        c["msg"] = ""
        _write_json(CRASH_FILE, c)
    return msg, n


def clear_crash_count():
    c = _read_json(CRASH_FILE)
    if c.get("n"):
        c["n"] = 0
        _write_json(CRASH_FILE, c)
