"""
mqtt_client.py - MQTT connection management: connect, the top-level message
callback, failure-proof publish helpers, and periodic time/awake-state publishing.

3.10: MQTT is best-effort. Nothing in here raises on a network problem and
nothing reconnects on its own - Detect.py's main loop owns reconnection (rate
limited), so a broker outage can never stop the motion monitoring.
"""
import time
from umqtt_simple import MQTTClient
import state
import diag

# Connection-state bookkeeping, used only to log up/down TRANSITIONS (not every
# failed publish) so the small log files are not flooded during an outage.
_up = False
_down_at = None


def _set_up(is_up, reason=""):
    global _up, _down_at
    if is_up:
        if not _up:
            if _down_at is not None:
                secs = time.ticks_diff(time.ticks_ms(), _down_at) // 1000
                diag.log("info", "MQTT restored after %s" % diag.fmt_duration(secs))
            else:
                diag.log("info", "MQTT connected")
        _down_at = None
    else:
        if _up:
            diag.log("error", "MQTT lost: %s" % reason)
        if _down_at is None:
            _down_at = time.ticks_ms()
    _up = is_up


def mqtt_down_seconds():
    """Seconds MQTT has been down, or None if it is currently up."""
    if _up or _down_at is None:
        return None
    return time.ticks_diff(time.ticks_ms(), _down_at) // 1000


def drop_client(reason):
    """Discard the current (dead) MQTT client. Safe to call when it is already None."""
    c = state.client
    state.client = None
    if c is not None:
        try:
            c.disconnect()
        except Exception:
            pass  # Socket is already dead, ignore failure to disconnect cleanly
    _set_up(False, reason)


def safe_publish(topic, payload, retain=False, qos=0):
    """Publish without ever raising. Returns True if the publish went out.
    On any failure the client is dropped so the main loop reconnects it."""
    c = state.client
    if c is None:
        return False
    try:
        if isinstance(payload, str):
            payload = payload.encode()
        c.publish(topic, payload, retain=retain, qos=qos)
        return True
    except Exception as e:
        drop_client("publish: %s: %s" % (type(e).__name__, e))
        return False


def termMsg(topic, message_text):
    #These are now going to the MQTT broker
    print(topic, message_text)
    safe_publish(topic, message_text)

def mqtt_callback(topic, msg):
    try:
        msg_str = msg.decode()
    except Exception:
        print("MQTT decode error")
        return

    handler = state.command_handlers.get(topic)

    if handler:
        try:
            handler(msg_str)
        except Exception as e:
            # The broker is public: a malformed command must never be able to crash the detector.
            diag.log("error", "Command handler failed (%s): %s: %s" % (str(topic), type(e).__name__, e))
            safe_publish(state.topics["topic_err"], b"ERR: command failed")
    else:
        print("Unknown topic:", topic)
        safe_publish(state.topics["topic_err"], b"ERR: unknown topic")

def connect_mqtt(retries=5, delay=2, timeout=5):
    client = MQTTClient(
        state.client_id,
        state.mqtt_server,
        user='PicoMan',
        password='Pico2Stuff',
        port=1883,
        ssl=False,
        keepalive=60
    )

    client.set_callback(mqtt_callback)

    for attempt in range(1, retries + 1):
        try:
            print(f"Connecting to MQTT broker (Attempt {attempt}/{retries})...")
            # Some micro-libraries return a status code (0 = success)
            res = client.connect(timeout=timeout)

            if res == 0 or res is None:
                print(f'Successfully connected to {state.mqtt_server} as {state.client_id}')
                client.subscribe(state.topics["topic_cmd_all"])
                print(f'Subscribed to {state.topics["topic_cmd_all"]}')
                _set_up(True)
                return client
            else:
                print(f"Broker rejected connection with code: {res}")

        except OSError as e:
            # Catches network errors, connection timeouts, and unreachable hosts
            print(f"Network error during connection: {e}")
        except Exception as e:
            # Catches other unexpected errors without blocking KeyboardInterrupt
            print(f"Unexpected error: {e}")

        if attempt < retries:
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)

    # If we exhaust all retries, handle the failure explicitly
    print("Failed to connect to MQTT broker after multiple attempts.")
    return None

def publish_time():
    """Publish time, awake flag and alert status. Returns True if all went out.
    Reconnection is handled by Detect.py's main loop, not here."""
    # Get the current Unix timestamp
    current_seconds = time.time()

    # Convert to a local time tuple
    # The tuple contains: (year, month, mday, hour, minute, second, weekday, yearday)
    time_tuple = time.localtime(current_seconds)

    # Extract individual components for easier formatting
    year, month, day, hour, minute, second, weekday, yearday = time_tuple

    # Example: Basic HH:MM:SS format
    formatted_time_basic = "{:02d}:{:02d}:{:02d}".format(hour, minute, second)

    # Get the True/False value for awake state and convert it to text
    awake_payload = str(state.rtc_manager.is_in_awake_window(state.myConfig.config["awake_window"]))
    # Current alert status, mirrored from Detect.py's main loop via state.alert_active
    alert_payload = b"SET" if state.alert_active else b"CLEAR"

    ok = safe_publish(state.topics["topic_time"], formatted_time_basic.encode())
    ok = safe_publish(state.topics["topic_awake"], awake_payload) and ok
    ok = safe_publish(state.topics["topic_alert"], alert_payload, retain=False, qos=0) and ok
    return ok
