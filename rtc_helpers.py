"""
rtc_helpers.py - Real-time-clock sync helper.
"""
import state
from mqtt_client import termMsg

def init_RTC(secrets):
    #Expects that internet is connected.

    #Getting time from internet returns a time adjusted for time zone and daylight savings.
    #If there's not internet connection the return value will be my birthday in the year 2000
    termMsg(state.topics["topic_log"], f"Getting time (main) from{secrets['dateTime']['host']}.")
    state.log_file.debug(f"Getting time (main) from{secrets['dateTime']['host']}.")
    # Wrap in try/except — time_from_internet can throw OSError -2 (DNS failure)
    # even when WiFi and MQTT are connected. In that case we fall back to the
    # birthday sentinel (year 2000) so the main loop knows to retry later.
    try:
        state.rtc_manager.setRTC(state.internet_manager.time_from_internet(secrets["dateTime"]["host"], secrets["dateTime"]["API_KEY"]))
    except OSError as e:
        state.log_file.error(f"init_RTC: DNS/network error getting time: {e}. Will retry in main loop.")
        termMsg(state.topics["topic_log"], f"init_RTC: time sync failed (OSError {e}), will retry.")
        # Leave RTC at birthday sentinel — main loop NeedUpdate logic will retry

    return #No value is returned, but the RTC value (inside that class) will be updated.
