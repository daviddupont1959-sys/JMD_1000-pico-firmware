"""
mqtt_handlers.py - Handlers for each MQTT command topic.

Importing this module has a side effect: it registers the dispatch table
into state.command_handlers (see bottom of file), so it must be imported
once during boot (Detect.py does this) even though nothing else calls into
it directly by name.
"""
import time
import os
import machine
import ujson as json
import ota_update
import state
from notify import send_inactivity_alert
from rtc_helpers import init_RTC

#This appears to be working, just be careful with JSON string formation.
def handle_set_config(msg_str):
    print(f"Config JSON: {msg_str}")
    try:
        update = json.loads(msg_str)

        # Deep merge helper - only allows updating keys that already exist
        # in the config schema. Rejects unknown keys instead of silently
        # creating orphaned top-level entries (e.g. sending "inactivity_alert"
        # instead of the correct "thresholds.inactivity_alert").
        def merge_dict(base, new, path=""):
            for key, value in new.items():
                full_path = f"{path}.{key}" if path else key
                if key not in base:
                    raise KeyError(f"unknown config key '{full_path}'")
                if isinstance(value, dict):
                    if not isinstance(base[key], dict):
                        raise KeyError(f"'{full_path}' is not a nested setting")
                    merge_dict(base[key], value, full_path)
                else:
                    if isinstance(base[key], dict):
                        raise KeyError(f"'{full_path}' is a nested setting, not a single value")
                    base[key] = value

        # Work on a copy first so a partial failure doesn't leave the
        # live config half-updated. (json round-trip avoids needing the
        # 'copy' module, which isn't guaranteed to be on MicroPython.)
        trial = json.loads(json.dumps(state.myConfig.config))
        merge_dict(trial, update)

        state.myConfig.config = trial
        state.myConfig._sanitize()
        state.myConfig.save_config()

        state.client.publish(state.topics["topic_ack"], b"OK: config updated")
    except Exception as e:
        state.client.publish(state.topics["topic_err"], f"ERR: {e}".encode())

#This seems to be working. (I think I saw the alert go back to CLEAR once)
def handle_test_alert(msg_str):
    state.client.publish(state.topics["topic_alert"], b"TEST")
    state.client.publish(state.topics["topic_ack"], b"OK: test alert sent")
    send_inactivity_alert("Alert Testing Requested.")


#This seems to be working
def handle_reboot(msg_str):
    state.client.publish(state.topics["topic_ack"], b"OK: rebooting")
    time.sleep(1)
    machine.reset()


#This would require the phone to send the current timestamp as {"epoch": <unix_timestamp>}
def handle_sync_time(msg_str):
    try:
        init_RTC(state.secrets)
        if state.rtc_manager.get_time_part("year") != 2000:
            state.client.publish(state.topics["topic_ack"], 
                f"OK: time synced to {state.rtc_manager.get_formatted_time()}".encode())
        else:
            state.client.publish(state.topics["topic_err"], 
                b"ERR: time sync failed - could not reach time server")
    except Exception as e:
        state.client.publish(state.topics["topic_err"], f"ERR: time sync failed: {e}".encode())

#This seems to be working
def handle_cfg_get(msg_str):
    path = (msg_str or "").strip()

    # No path given -> return the full config (original behavior)
    if not path:
        cfg_json = state.myConfig.get_config()
        state.client.publish(state.topics["topic_cfg_full"], cfg_json.encode())
        return

    # Walk the dot-path, e.g. "thresholds.inactivity_alert"
    keys = path.split(".")
    node = state.myConfig.config
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            state.client.publish(
                state.topics["topic_err"],
                f"ERR: unknown config key '{path}'".encode()
            )
            return

    # Found it - report back the single value
    try:
        value_json = json.dumps({path: node})
    except Exception:
        value_json = json.dumps({path: str(node)})
    state.client.publish(state.topics["topic_cfg_full"], value_json.encode())


def handle_update(msg_str):
    try:
        state.client.publish(state.topics["topic_ack"], b"OTA: starting update check...")
        result = ota_update.check_and_update()
        state.client.publish(state.topics["topic_ack"], str(result).encode())
        if result["updated"] and not result["errors"]:
            time.sleep(2)  # give broker time to receive the ack before rebooting
            machine.reset()
    except Exception as e:
        state.client.publish(state.topics["topic_err"], f"ERR: OTA failed: {e}".encode())

def handle_version(msg_str):
    # Send the firmware version to the state/version topic
    state.client.publish(state.topics["topic_version"], f"{state.FW_REV}".encode(), retain=True, qos=1)
    state.client.publish(state.topics["topic_ack"], f"OK: firmware version {state.FW_REV}".encode())

def handle_get_log_list(msg_str):
    # Respond with a JSON list of log files (mirrors the BLE "DIR" command)
    try:
        files = os.listdir()
        log_files = [f for f in files if f.startswith('log')]
        payload = json.dumps(log_files).encode()

        # Log locally so this is independently verifiable even if the MQTT
        # publish itself goes missing (retrieve via topic_get_log_file).
        state.log_file.debug(f"get_log_list: sending {len(log_files)} file(s): {log_files}")

        # qos=1 so the broker must acknowledge receipt - qos=0 publishes are
        # fire-and-forget and can be silently dropped, especially on a
        # shared public broker like broker.emqx.io.
        state.client.publish(state.topics["topic_log_list"], payload, qos=1)
        time.sleep_ms(50)  # avoid sending two publishes back-to-back
        state.client.publish(state.topics["topic_ack"], f"OK: {len(log_files)} log file(s) found".encode())
    except Exception as e:
        state.client.publish(state.topics["topic_err"], f"ERR: listing log files failed: {e}".encode())

def handle_get_log_file(msg_str):
    # msg_str contains the filename to retrieve (mirrors the BLE "FIL" command)
    filename = (msg_str or "").strip()

    if not filename:
        state.client.publish(state.topics["topic_err"], b"ERR: no filename supplied")
        return

    # Basic sanity check - only allow plain log filenames, no path traversal
    if not filename.startswith('log') or '/' in filename or '\\' in filename or '..' in filename:
        state.client.publish(state.topics["topic_err"], f"ERR: invalid log filename '{filename}'".encode())
        return

    try:
        with open(filename, 'r') as f:
            contents = f.read()
        payload = json.dumps({"filename": filename, "contents": contents}).encode()
        state.client.publish(state.topics["topic_log_file"], payload, qos=1)
        time.sleep_ms(50)  # avoid sending two publishes back-to-back
        state.client.publish(state.topics["topic_ack"], f"OK: sent log file '{filename}'".encode())
        state.log_file.info(f"Log file {filename} sent via MQTT.")
    except OSError as e:
        state.client.publish(state.topics["topic_err"], f"ERR: could not read '{filename}': {e}".encode())

# ----------------------------------------------------
# COMMAND DISPATCHER
# ----------------------------------------------------
# Registered into shared state (see state.py) rather than exported
# directly, so mqtt_client.mqtt_callback can look it up without importing
# this module - this module already depends indirectly on mqtt_client (via
# rtc_helpers), so an import back from mqtt_client would be circular.
state.command_handlers = {
    state.topics["topic_set_config"]: handle_set_config,
    state.topics["topic_test_cmd"]: handle_test_alert,
    state.topics["topic_reboot"]: handle_reboot,
    state.topics["topic_sync_time"]: handle_sync_time,
    state.topics["topic_cfg_get"]: handle_cfg_get,
    state.topics["topic_update"]: handle_update,
    state.topics["topic_get_version"]: handle_version,
    state.topics["topic_get_log_list"]: handle_get_log_list,
    state.topics["topic_get_log_file"]: handle_get_log_file,
}
