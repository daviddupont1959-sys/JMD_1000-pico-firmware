"""
monitor.py - Runtime helpers for Detect.py (new in 3.10).

Split out of Detect.py so each file stays small enough to import comfortably on
the Pico and can be OTA-updated on its own. Contains the tunable timing
constants, WiFi / MQTT upkeep, the notification retry queue, last-motion
persistence and the hourly heartbeat log line. Detect.py keeps the main loop.
"""
import gc
import time
import machine

import state
import notify
import diag
from mqtt_client import connect_mqtt, termMsg, safe_publish, drop_client, mqtt_down_seconds
from rtc_helpers import init_RTC

# ---- Tunables ----
DEFAULT_THRESHOLD_S = 4 * 3600  # Used only if the configured inactivity threshold is unusable
TIME_PUBLISH_MS     = 15000     # How often time / awake / alert status go to MQTT
PING_MS             = 30000     # MQTT keep-alive (broker keepalive is 60 s)
MQTT_RETRY_MS       = 60000     # Minimum spacing between MQTT reconnect attempts (each blocks the loop up to 3 s)
WIFI_RETRY_MS       = 120000    # Minimum spacing between WiFi reconnect attempts (each blocks the loop ~20 s)
NOTIFY_RETRY_MS     = 60000     # Spacing between notification retries
HEARTBEAT_MS        = 3600000   # DEBUG heartbeat line in the log file (1 hour)
PERSIST_MS          = 900000    # Save last-motion time to flash at most every 15 minutes
STABLE_MS           = 600000    # Run this long without crashing -> clear crash counter
RESTORE_MAX_AGE_S   = 12 * 3600 # Ignore a saved last-motion time older than this

# ---- Helpers ----
# Timers and counters shared by the helpers below. A dict is used so the
# helpers can update them without 'global' declarations.
_now0 = time.ticks_ms()
t = {
    "mqtt_next": _now0,                            # next time an MQTT reconnect may be tried
    "ping": _now0,                                 # last keep-alive
    "time_pub": time.ticks_add(_now0, -TIME_PUBLISH_MS),  # last time/awake/alert publish (due now)
    "hb": _now0,                                   # last heartbeat log line
    "since_persist": PERSIST_MS,                   # ms since last-motion was saved (due on first motion)
    "wifi_down": None,                             # tick when WiFi was lost (None = up)
    "wifi_next": _now0,                            # next time a WiFi reconnect may be tried
    "edges": 0,                                    # PIR rising edges since last heartbeat
    "stable_done": False,                          # crash counter cleared yet?
    "cfg": None,                                   # last known config snapshot (see check_config_change)
    "cfg_chk": _now0,                              # last time the config was compared
}

# Notifications waiting for delivery: dicts of kind ("set","clear","info"), text, tries, next.
pending = []


def fmt_hms(total_seconds):
    return "{:02d}:{:02d}:{:02d}".format(total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60)


def reset_cause_name():
    try:
        cause = machine.reset_cause()
    except Exception:
        return "unknown"
    for name in ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "SOFT_RESET", "DEEPSLEEP_RESET"):
        if hasattr(machine, name) and getattr(machine, name) == cause:
            return name
    return str(cause)


def guarded(name, fn, *args):
    """Run a non-critical service call. If it raises, log (rate limited) and carry
    on - the motion monitoring must keep running."""
    try:
        return fn(*args)
    except Exception as e:
        diag.log_limited("exc_" + name, "error", "%s failed: %s: %s" % (name, type(e).__name__, e), 600)
        return None


def threshold_s():
    """Configured inactivity threshold in seconds; falls back to 4 hours if the
    stored value is unusable (a bad value must not crash or disable the alert)."""
    try:
        v = int(state.myConfig.config["thresholds"]["inactivity_alert"])
        if v > 0:
            return v
    except Exception:
        pass
    diag.log_limited("bad_threshold", "error", "Bad inactivity_alert in config; using %d s" % DEFAULT_THRESHOLD_S, 3600)
    return DEFAULT_THRESHOLD_S


def awake_now():
    """True if inside the awake window. If the window is unusable, fail safe: treat as awake."""
    try:
        return state.rtc_manager.is_in_awake_window(state.myConfig.config["awake_window"])
    except Exception as e:
        diag.log_limited("bad_window", "error", "Bad awake_window in config (%s); treating as awake" % e, 3600)
        return True


def sync_clock():
    """Get local time from the internet. Returns True if the RTC now holds a real time."""
    before = getattr(state.rtc_manager, "time_Updated", None)
    gc.collect()  # the time lookup is an HTTPS request and needs a big block of RAM
    try:
        init_RTC(state.secrets) #Gets local time from the internet, requires internet connection
    except Exception as e:
        diag.log_limited("rtc_exc", "error", "Clock sync raised %s: %s" % (type(e).__name__, e), 900)
    if state.rtc_manager.get_time_part("year") == 2000:
        diag.log_limited("rtc_fail", "error", "Clock sync failed (internet time unavailable)", 900)
        return False
    # setRTC() stores a new list in time_Updated on every real sync. If it is still
    # the same object, init_RTC failed (and logged why) and the clock was NOT updated.
    if state.rtc_manager.time_Updated is before:
        diag.log_limited("rtc_kept", "debug", "Clock sync did not complete; keeping current time", 900)
        return True
    termMsg(state.topics["topic_log"], f"Time updated from Internet at {state.rtc_manager.get_formatted_time()}.")
    diag.log("debug", "Clock synced: %s" % state.rtc_manager.get_formatted_time())
    return True


# ---- notifications with retry ------------------------------------------------
def try_pending(now):
    """Send any queued notification that is due. At most one blocking send per call."""
    if not pending:
        return
    if not state.internet_manager.is_connected():
        return  # WiFi is down; the notification waits (and retries once WiFi is back)
    for item in list(pending):
        if time.ticks_diff(now, item["next"]) < 0:
            continue
        ok = notify.send_inactivity_alert(item["text"])
        item["tries"] += 1
        if ok:
            pending.remove(item)
            diag.log("alert" if item["kind"] in ("set", "clear") else "info",
                     "Notification delivered (%s, attempt %d)" % (item["kind"], item["tries"]))
        else:
            if item["kind"] == "info" and item["tries"] >= 5:
                pending.remove(item)
                diag.log("error", "Giving up on start-up notification after %d attempts (%s)" % (item["tries"], notify.last_error))
            else:
                delay = NOTIFY_RETRY_MS  # alerts are retried every minute until delivered
                item["next"] = time.ticks_add(now, delay)
                diag.log_limited("notify_fail_" + item["kind"], "error",
                                 "Notification failed (%s, attempt %d, %s); retrying every %d s"
                                 % (item["kind"], item["tries"], notify.last_error, delay // 1000), 900)
        break


def queue_notification(kind, text):
    """Queue a notification and try to send it right away.
    An 'alert cancelled' notice for an alert whose notification never went out
    cancels both rather than sending a confusing stale pair."""
    if kind == "clear":
        for item in pending:
            if item["kind"] == "set":
                pending.remove(item)
                diag.log("alert", "Alert cleared before its notification was delivered; both notifications dropped")
                return
    pending.append({"kind": kind, "text": text, "tries": 0, "next": time.ticks_ms()})
    try_pending(time.ticks_ms())


# ---- WiFi / MQTT upkeep --------------------------------------------------------
def wifi_check(now):
    """Returns True if WiFi is up. Reconnects (rate limited) and logs transitions."""
    im = state.internet_manager
    if im.is_connected():
        if t["wifi_down"] is not None:
            secs = time.ticks_diff(now, t["wifi_down"]) // 1000
            diag.log("info", "WiFi restored after %s" % diag.fmt_duration(secs))
            t["wifi_down"] = None
            state.leds["WIFI"].on()
        return True
    if t["wifi_down"] is None:
        t["wifi_down"] = now
        t["wifi_next"] = now          # first retry immediately
        state.leds["WIFI"].off()
        diag.log("error", "WiFi lost")
    if time.ticks_diff(now, t["wifi_next"]) >= 0:
        t["wifi_next"] = time.ticks_add(now, WIFI_RETRY_MS)
        try:
            im.connect()
        except Exception as e:
            diag.log_limited("wifi_retry", "debug", "WiFi reconnect failed: %s" % e, 900)
    return False


def service_mqtt(now):
    """Keep MQTT alive without ever blocking or crashing the monitoring loop."""
    if not wifi_check(now):
        if state.client is not None:
            drop_client("WiFi down")
        return

    if state.client is None:
        if time.ticks_diff(now, t["mqtt_next"]) >= 0:
            t["mqtt_next"] = time.ticks_add(now, MQTT_RETRY_MS)
            c = connect_mqtt(retries=1, timeout=3)  # short: the PIR is not read while this blocks
            if c is not None:
                state.client = c
                safe_publish(state.topics["topic_version"], f"{state.FW_REV}".encode(), retain=True, qos=1)
                t["ping"] = now
                t["time_pub"] = time.ticks_add(now, -TIME_PUBLISH_MS)  # publish status right away
            else:
                diag.log_limited("mqtt_retry", "debug", "MQTT reconnect failed (down %s)" % diag.fmt_duration(mqtt_down_seconds() or 0), 1800)
        return

    #Check if there's any requests from the MQTT broker
    try:
        state.client.check_msg()
    except Exception as e:
        drop_client("check_msg: %s: %s" % (type(e).__name__, e))
        t["mqtt_next"] = now          # first reconnect attempt on the next pass
        return

    # Keep-alive ping so the broker doesn't drop the connection / subscription.
    if time.ticks_diff(now, t["ping"]) >= PING_MS:
        t["ping"] = now
        try:
            state.client.ping()
        except Exception as e:
            drop_client("ping: %s: %s" % (type(e).__name__, e))
            t["mqtt_next"] = now


def persist_motion(force=False):
    """Remember when motion was last seen, so a restart doesn't forget it.
    Throttled to protect the flash (the saved time can only be EARLIER than the
    truth, so after a restart the alert can only come early, never late)."""
    if force or t["since_persist"] >= PERSIST_MS:
        t["since_persist"] = 0
        diag.save_runstate({"last_motion": time.time()})


def window_info(win):
    """Describe the awake window. Returns (text, warning_or_None).
    A window whose end is earlier than its start crosses midnight, so a single
    mistyped hour (e.g. 3:57 instead of 15:57) silently turns a daytime window
    into a ~21-hour one. Flag windows that are very long or very short."""
    try:
        s = int(win["start"]["hour"]) * 60 + int(win["start"]["minute"])
        e = int(win["end"]["hour"]) * 60 + int(win["end"]["minute"])
        mins = (e - s) % 1440 or 1440
        text = "%02d:%02d-%02d:%02d (%s awake)" % (s // 60, s % 60, e // 60, e % 60, diag.fmt_duration(mins * 60))
        warn = None
        if mins > 20 * 60 or mins < 6 * 60:
            warn = "Awake window %s looks unusual - check the start and end times." % text
        return text, warn
    except Exception as ex:
        return "unreadable", "Awake window could not be read (%s)." % ex


def _cfg_snapshot():
    c = state.myConfig.config
    th = c.get("thresholds")
    return {
        "inactivity_alert": str(th.get("inactivity_alert") if isinstance(th, dict) else None),
        "awake_window": window_info(c.get("awake_window"))[0],
        "location": str(c.get("location")),
        "log_level": str(c.get("log_level")),
    }


def check_config_change(now):
    """Every 10 s, compare the settings that matter with the last known values and
    log any change (old -> new). Works no matter which path changed the config
    (BLE app, MQTT command, file edit + reload), so the log answers 'when did
    this setting change?'."""
    if t["cfg"] is not None and time.ticks_diff(now, t["cfg_chk"]) < 10000:
        return
    t["cfg_chk"] = now
    snap = _cfg_snapshot()
    old = t["cfg"]
    t["cfg"] = snap
    if old is None:
        return
    changes = ["%s %s -> %s" % (k, old[k], snap[k]) for k in snap if snap[k] != old[k]]
    if changes:
        diag.log("info", "Config changed: " + "; ".join(changes))
        if snap["awake_window"] != old["awake_window"]:
            warn = window_info(state.myConfig.config.get("awake_window"))[1]
            if warn:
                diag.log("error", warn)


def heartbeat(now, quiet_ms_val, uptime_ms_val, seen):
    """One DEBUG line per hour: proof the loop was alive, plus the numbers needed
    to judge sensor behaviour (PIR edges per hour) and connectivity."""
    if time.ticks_diff(now, t["hb"]) < HEARTBEAT_MS:
        return
    t["hb"] = now
    gc.collect()  # measure free RAM after cleanup, otherwise the number depends on GC timing
    down = mqtt_down_seconds()
    diag.log("debug", "HB up=%s quiet=%s edges=%d seen=%d awake=%d thr=%d wifi=%d mqtt=%s free=%dK" % (
        diag.fmt_duration(uptime_ms_val // 1000), diag.fmt_duration(quiet_ms_val // 1000),
        t["edges"], 1 if seen else 0, 1 if awake_now() else 0, threshold_s(),
        1 if state.internet_manager.is_connected() else 0,
        "up" if down is None else "down" + diag.fmt_duration(down), gc.mem_free() // 1024))
    t["edges"] = 0


