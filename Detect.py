''' Motion Detector Firmware
11/02/2025 - Start of complete re-write
                This code will be based on a Miro flow diagram
                https://miro.com/app/board/uXjVJDQWxEE=/
            Of course copying libraries and functions where it makes sense.

2026 - Split into smaller modules (state, utils, notify, mqtt_client,
       mqtt_handlers, ble_handlers, rtc_helpers) so each file OTA-updates
       independently and stays well under the size of the old monolith.

3.10 - Reliability release. See Version_History.txt for the full list. In short:
       MQTT is best-effort (a broker outage can no longer stop the monitoring),
       failed alert notifications are retried, "how long has it been quiet" survives
       a restart, loop timing no longer depends on hitting an exact clock second,
       and the important events are written to the log file at sensible levels.
'''

# Includes
from iClk import RTCManager
from iNet import InternetTimeoutError
from iNet import InternetManager
from iBLE import BLESimplePeripheral
from iCFG import ConfigManager
from iLogFile import LogManager
import secure_config

import machine
import bluetooth
import time

import state
import utils
import diag
import mqtt_handlers   # side effect: registers state.command_handlers
from mqtt_client import connect_mqtt, publish_time, termMsg, safe_publish, drop_client
from monitor import (TIME_PUBLISH_MS, STABLE_MS, RESTORE_MAX_AGE_S, t, fmt_hms, reset_cause_name,
                     guarded, threshold_s, awake_now, sync_clock, try_pending, queue_notification,
                     service_mqtt, persist_motion, heartbeat, window_info, check_config_change)
from ble_handlers import on_rx, allow_bt

'''
# Variable Definition & Hardware Configuration Loading
'''
rtc_value = [2000,2,3,8,35,0] # My birthday in the year 2000 (RP2 didn't like 1959!)

# Hardware wiring and unit identity come from hwconfig.py (via state.py), which picks
# this board's entry in HW_Config.json by its unique ID. If the board is not listed,
# or the file is unusable, state.hw_warning says so and the default settings are used.
hw_config = state.hw_config
hw_note = state.hw_warning

# Define the pins dynamically from the loaded configuration
motion_detector_pin = machine.Pin(hw_config["motion_detector_pin"], machine.Pin.IN, machine.Pin.PULL_DOWN)

state.leds = {
    name: machine.Pin(pin_num, machine.Pin.OUT)
    for name, pin_num in hw_config["leds"].items()
}

utils.lamp_test()

#Start by assuming no motion or alert
detector_state = alert_state = False
#Make sure there's no motion or alert flags set.
motion_flag = alert_flag = False
alert_condition = False
# Tracks whether motion has been seen at least once since boot (or restored from
# before a restart). Prevents a false alert if the device starts up during a quiet period.
ever_seen_motion = False

#This is loaded from an encrypted file.
state.secrets = secure_config.load_config("secrets.enc") # E-mail and geo-location creds.

'''
# INIT Classes
#   Configuration manager
#   Log File manager
#   Internet manager
#   Real Time Clock (RTC) manager (requires internet)
#   Bluetooth handler
'''
#   Configuration manager
state.myConfig = ConfigManager()
''' Configuration needs to be loaded first because... 
    other class setup calls require information
    from the configuration file.
'''
state.myConfig.load_config() # If the file doesn't exist, the default data is written

#   Log File manager
# Set up to log information
'''
Supports 4 types of messages in this order
        log_file.debug(message)
        log_file.info(message)
        log_file.alert(message)
        log_file.error(message)
'''
state.log_file = LogManager(level=state.myConfig.config["log_level"], max_size=2048)
diag.log("info", "Hardware: board=%s unit=%s config=%s PIR=GPIO%d" % (
    state.board_id, state.client_id, state.hw_source, hw_config["motion_detector_pin"]))
if hw_note:
    diag.log("error", hw_note)

#   Internet manager
#Get wifi connection info from encrypted file.
#It's a dictionary with two entries: ssid, and password
state.WiFi_Creds = secure_config.load_config("wifi.enc")
state.internet_manager = InternetManager(state.WiFi_Creds["ssid"], state.WiFi_Creds["password"], state.leds["WIFI"])
#Force a disconnect in case there's a leftovcer connetcion
state.internet_manager.disconnect()

#   Real Time Clock (RTC) manager (requires internet)
state.rtc_manager = RTCManager()

#   Bluetooth handler
state.ble_manager = BLESimplePeripheral(bluetooth.BLE(), led_pin=state.leds["BlueTooth"], name="Motion")
# Define the call back routine for incoming data.
state.ble_manager.on_write(on_rx)


'''
# Hardware INIT
'''

#Make sure all LEDs start in the off state
for name, led in state.leds.items(): led.off()

'''
# Main code
'''
# Turn on Working LED
state.leds["Working"].on()

#Allow for a bluetooth connection for 15 seconds
allow_bt(ble_interval = 15)

# WiFi is required: without it nothing can be reported, and the BLE window lets
# the credentials be fixed. MQTT is best-effort: if the broker can't be reached
# the monitoring starts anyway and the main loop keeps retrying.
while True: #This acts like a repeat / until loop
    try:
        state.internet_manager.connect() #See if internet is connected
    except InternetTimeoutError:
        diag.log("error", "WiFi connect timed out at boot; opening BLE window")
        allow_bt(ble_interval = 15) #Otherwise, allow bluetooth connection (potentially modifying wifi creds)
        continue #Which will try to connect again
    except Exception as e:
        diag.log("error", "WiFi connect error at boot: %s: %s" % (type(e).__name__, e))
        time.sleep(5)
        continue
    break
diag.log("info", "WiFi connected at boot")

for _attempt in range(2):
    _c = connect_mqtt(retries=2)  # Returns None (it does not raise) if it can't connect
    if _c is not None:
        state.client = _c
        break
    time.sleep(2)

if state.client is not None:
    safe_publish(state.topics["topic_log"], f'Hello from {state.client_id}!'.encode())
    # Send the firmware version immediately on boot/reconnect and retain it
    safe_publish(state.topics["topic_version"], f"{state.FW_REV}".encode(), retain=True, qos=1)
    #Make sure we start by clearing any alerts
    safe_publish(state.topics["topic_alert"], b"CLEAR", retain=False, qos=0)
else:
    drop_client("could not connect at boot")
    diag.log("error", "MQTT unavailable at boot; monitoring starts anyway, will keep retrying")

#Sync the RTC with real time BEFORE issuing any startup log entries or
#notifications, so those messages carry an accurate timestamp instead of
#the year-2000 placeholder. Retry a few times before giving up.
rtc_synced = False
for _rtc_attempt in range(5):
    if sync_clock():
        rtc_synced = True
        break
    print("Getting time from internet failed. Retrying before startup notification...")
    time.sleep(5)

if not rtc_synced:
    print("Unable to sync RTC before startup notification; timestamps may be inaccurate until next sync.")

#Report the start-up. If the previous run crashed (main.py recorded it), say so.
crash_msg, crash_n = diag.take_crash_message()
cause = reset_cause_name()
diag.log("info", f"Detector started. Firmware: {state.FW_REV}. Reset cause: {cause}.")
win_text, win_warn = window_info(state.myConfig.config.get("awake_window"))
diag.log("info", "Config: unit=%s inactivity_alert=%ds awake_window=%s location=%s" % (
    state.client_id, threshold_s(), win_text, state.myConfig.config.get("location")))
start_text = f"Motion detector started at {state.myConfig.config['location']}. Firmware: {state.FW_REV}."
if win_warn:
    diag.log("error", win_warn)
    start_text += " " + win_warn
if hw_note:
    start_text += " " + hw_note
if crash_msg:
    short = " | ".join([ln.strip() for ln in crash_msg.split("\n") if ln.strip()][-2:])
    diag.log("error", "Previous run crashed (crash #%d): %s" % (crash_n, short))
    start_text += f" Restarted after a crash (#{crash_n}): {short}"
elif cause not in ("PWRON_RESET", "unknown"):
    start_text += f" Reset cause: {cause}."
queue_notification("info", start_text)

# Restore "how long has it been quiet" from before a restart, so a reboot while
# the house is empty doesn't silently cancel the alert.
quiet_ms = 0
uptime_ms = 0
if rtc_synced:
    try:
        age = time.time() - int(diag.load_runstate().get("last_motion"))
    except Exception:
        age = None
    if age is not None and 0 <= age <= RESTORE_MAX_AGE_S:
        quiet_ms = int(age * 1000)
        ever_seen_motion = True
        diag.log("info", "Restored last motion from before restart: %s ago" % diag.fmt_duration(age))
    else:
        diag.log("info", "No usable last-motion record; waiting for first motion")
prev_tick = time.ticks_ms()
last_awake = None

#Enter the main endless while loop
while True:
    #Get the location specific time from the internet
    #Set up the real time clock (RTC)
    #This is redundant the first time, but necessary for SundayMorning or NeedUpdate triggers.
    if not sync_clock():
        termMsg(state.topics["topic_log"], "Getting time from internet failed. Waiting 1 minute")
        time.sleep(60)  #Wait for 1 minute
        continue        #Try again

    #First sub-loop
    while True: #Get time from internet every Sunday at 2:01 AM, in case Daylight Savings changed.
        #Apparently weekday 0 = Monday, therefore Sunday is 6.
        #The AND part should prevent updating more than once a day
        #When for example the clocks go back to standard time
        SundayMorning = state.rtc_manager.check_time(6,2,1) and state.rtc_manager.time_Updated[2] != state.rtc_manager.get_time_part("date")
        #This is here in case the most recent update returned the year 2000 which indicates internet connection didn't work.
        NeedUpdate = state.rtc_manager.get_time_part("year") == 2000 and state.rtc_manager.get_time_part("minute") % 15 == 0 and state.rtc_manager.get_time_part("second") % 60 == 0

        if SundayMorning or NeedUpdate:
            break #Exits current loop and causes parent loop to go to next iteration. Which starts by getting the time.

        # NOTE (3.10): the quiet timer (quiet_ms) is no longer reset here. It only
        # resets when motion is seen, so clock re-syncs can't postpone an alert.

        #Second sub-loop
        while True:

            time.sleep(1) #1 second delay
            # Real elapsed time since the last pass, from the monotonic tick counter.
            # Unlike time.time() it is unaffected by clock re-syncs and DST changes,
            # and it includes time spent in slow network calls.
            now_ms = time.ticks_ms()
            delta_ms = time.ticks_diff(now_ms, prev_tick)
            prev_tick = now_ms
            quiet_ms += delta_ms
            uptime_ms += delta_ms
            t["since_persist"] += delta_ms
            #Toggle the working led
            state.leds["Working"].toggle()

            # Weekly clock re-sync (Sunday 2:01 AM) for daylight saving changes. In 3.03
            # this was only checked after motion; checking every pass makes it reliable.
            if state.rtc_manager.check_time(6,2,1) and state.rtc_manager.time_Updated[2] != state.rtc_manager.get_time_part("date"):
                diag.log("info", "Weekly clock re-sync due")
                break

            # WiFi / MQTT upkeep, notification retries, periodic publishes, heartbeat.
            # None of these may stop the motion monitoring.
            guarded("mqtt", service_mqtt, now_ms)
            guarded("notify", try_pending, now_ms)
            guarded("config", check_config_change, now_ms)

            # Send the current time / awake flag / alert status to MQTT on a fixed
            # interval. (3.03 used time.time() % 15 == 0, which silently skips a
            # publish whenever a loop pass overruns the exact second.)
            if time.ticks_diff(now_ms, t["time_pub"]) >= TIME_PUBLISH_MS:
                t["time_pub"] = now_ms
                guarded("publish_time", publish_time)

            guarded("heartbeat", heartbeat, now_ms, quiet_ms, uptime_ms, ever_seen_motion)

            if not t["stable_done"] and uptime_ms >= STABLE_MS:
                t["stable_done"] = True
                diag.clear_crash_count()

            # Log the awake window opening / closing, so the log shows when alerts were possible.
            awake_flag = awake_now()
            if awake_flag != last_awake:
                if last_awake is not None:
                    diag.log("info", "Awake window %s (quiet for %s)" % ("opened" if awake_flag else "closed", diag.fmt_duration(quiet_ms // 1000)))
                last_awake = awake_flag

            detector_state = motion_detector_pin.value()	# Check Motion detector
            state.leds["Motion"].value(detector_state)
            # Send the current detector state to MQTT if it changed
            if (detector_state == 1) != motion_flag:
                if detector_state == 1:
                    t["edges"] += 1
                safe_publish(state.topics["topic_last_motion"], "ON" if detector_state == 1 else "OFF", retain=False, qos=0)

            if detector_state: #True - Motion was detected.
                was_quiet_s = quiet_ms // 1000
                quiet_ms = 0 #The time when motion was last detected
                motion_flag = True
                if not ever_seen_motion:
                    diag.log("info", "First motion since start-up")
                elif was_quiet_s >= 3600:
                    diag.log("info", "Motion detected after %s quiet" % diag.fmt_duration(was_quiet_s))
                ever_seen_motion = True
                persist_motion(force=(was_quiet_s >= 3600))

                #Turn off alert (if active). Checked against alert_flag (sticky -
                #only changes here and when the alert first fires) rather than
                #alert_condition, which is recomputed every tick below and can
                #silently flip back to False (e.g. awake_window ending) while the
                #ALERT LED is still on, which used to make this block get skipped.
                if alert_flag:
                    alert_flag = False
                    alert_condition = False
                    state.leds["ALERT"].off()
                    state.alert_active = False
                    # Need to send alert cancel message
                    diag.log("alert", "ALERT cleared! Motion detected after %s quiet" % diag.fmt_duration(was_quiet_s))
                    alert_message_text = f"At {state.rtc_manager.seconds_to_time(time.time())} motion was detected at {state.myConfig.config['location']}, alert cancelled."
                    termMsg(state.topics["topic_log"], alert_message_text)
                    queue_notification("clear", alert_message_text)
                    safe_publish(state.topics["topic_alert"], b"CLEAR", retain=False, qos=0)
                break #Return to first sub-loop

            else: #No motion at the moment
                quiet_time = quiet_ms // 1000
                alert_condition = (
                    ever_seen_motion        # don't alert before we've seen any motion at all
                    and (not motion_flag)   # no motion in the current check cycle
                    and (quiet_time >= threshold_s())
                    and awake_flag
                )

                if alert_condition and not alert_flag: # no motion for at least the alert_time, Notify!
                    alert_flag = True
                    state.leds["ALERT"].on()
                    state.alert_active = True
                    # Need to send alert active message
                    diag.log("alert", "ALERT Triggered! No motion for %s (threshold %s)" % (diag.fmt_duration(quiet_time), fmt_hms(threshold_s())))

                    # Format the threshold as HH:MM:SS
                    time_string = fmt_hms(threshold_s())

                    alert_message_text = f"At {state.rtc_manager.seconds_to_time(time.time())} no motion has been detected for {time_string} at {state.myConfig.config['location']}."
                    termMsg(state.topics["topic_log"], alert_message_text)
                    queue_notification("set", alert_message_text)  # retried until delivered
                    safe_publish(state.topics["topic_alert"], b"SET", retain=False, qos=0)

                motion_flag = False
                continue #Next iteration of second sub-loop
