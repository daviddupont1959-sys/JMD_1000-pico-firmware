"""
notify.py - Push notifications via ntfy.sh.

3.11: gc.collect() before the HTTPS request, and one immediate retry on ENOMEM
(out of memory), seen in the logs when two HTTPS calls ran back to back.

3.10: send_inactivity_alert() now returns True only when ntfy.sh acknowledged
the message (HTTP 200), and False otherwise, so the caller can retry. The
reason for the last failure is kept in notify.last_error for logging.
"""
import gc
import urequests
import state

last_error = ""


def _post(data, headers):
    # Newer urequests builds accept a timeout; older ones raise TypeError.
    try:
        return urequests.post(state.NTFY_URL, data=data, headers=headers, timeout=10)
    except TypeError:
        return urequests.post(state.NTFY_URL, data=data, headers=headers)


def send_inactivity_alert(message_text):
    """
    Sends a high-priority push notification to the family's phones.
    Used for inactivity alerts, "alert cancelled" notices and start-up notices.
    Returns True if delivered, False if not (never raises).
    """
    global last_error

    headers = {
        "Title": "🚨 DAD'S HOUSE ALERT 🚨",
        "Priority": "high",        # 'high' or '5' makes it stand out on the phone
        "Tags": "warning,house",   # Adds a warning emoji and house emoji automatically
    }

    response = None
    try:
        gc.collect()  # the HTTPS handshake needs a big block of RAM
        try:
            response = _post(message_text.encode(), headers)
        except OSError as e:
            if e.args and e.args[0] == 12:  # ENOMEM: free what we can and try once more
                gc.collect()
                response = _post(message_text.encode(), headers)
            else:
                raise
        if response.status_code == 200:
            print("Push notification delivered to ntfy.sh successfully.")
            last_error = ""
            return True
        last_error = "HTTP %s" % response.status_code
        print(f"Server responded with an error code: {response.status_code}")
        return False
    except Exception as e:
        # Network timeouts, Wi-Fi drops, DNS failures, out-of-memory, etc.
        last_error = "%s: %s" % (type(e).__name__, e)
        print(f"Failed to transmit push notification: {e}")
        return False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
