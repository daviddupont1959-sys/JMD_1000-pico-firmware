''' Motion Detector Firmware
11/02/2025 - Start of complete re-write
                This code will be based on a Miro flow diagram
                https://miro.com/app/board/uXjVJDQWxEE=/
            Of course copying libraries and functions where it makes sense.

2026 - Split into smaller modules (state, utils, notify, mqtt_client,
       mqtt_handlers, ble_handlers, rtc_helpers) so each file OTA-updates
       independently and stays well under the size of the old monolith.
'''

# Includes
from iClk import RTCManager
from iNet import InternetTimeoutError
from iNet import InternetManager
from iBLE import BLESimplePeripheral
from iCFG import ConfigManager
from iLogFile import LogManager
from umqtt_simple import MQTTException
import secure_config

import machine
import bluetooth
import time

import state
import utils
import notify
import mqtt_handlers   # side effect: registers state.command_handlers
from mqtt_client import connect_mqtt, publish_time, termMsg
from ble_handlers import on_rx, allow_bt
from rtc_helpers import init_RTC

'''
# Variable Definition
'''
rtc_value = [2000,2,3,8,35,0] # My birthday in the year 2000 (RP2 didn't like 1959!)

# Define the pins and names for the LEDs
state.leds = {
    "Working": machine.Pin(20, machine.Pin.OUT),
    "WIFI": machine.Pin(19, machine.Pin.OUT),
    "BlueTooth": machine.Pin(18, machine.Pin.OUT),
    "Motion": machine.Pin(17, machine.Pin.OUT),
    "ALERT": machine.Pin(16, machine.Pin.OUT)
}

utils.lamp_test()

#Start by assuming no motion or alert
detector_state = alert_state = False
#Make sure there's no motion or alert flags set.
motion_flag = alert_flag = False
alert_condition = False
# Tracks whether motion has been seen at least once since boot.
# Prevents a false alert if the device starts up during a quiet period.
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

#Set up the motion detector
motion_detector_pin = machine.Pin(28, machine.Pin.IN, machine.Pin.PULL_DOWN)

#Make sure all LEDs start in the off state
for name, led in state.leds.items(): led.off()

'''
# Main code
'''
# Turn on Working LED
state.leds["Working"].on()

#Allow for a bluetooth connection for 15 seconds
allow_bt(ble_interval = 15)
#Connect to internet and MQTT broker
while True: #This acts like a repeat / until loop
    #repeat
    try:
        state.internet_manager.connect() #See if internet is connected
    except InternetTimeoutError:
        allow_bt(ble_interval = 15) #Otherwise, allow bluetooth connection (potentially modifying wifi creds)
        continue #Which will try to connect again
    else: #Connection must have worked so... Connect to the MQTT Broker
        while True:
            try:
                state.client = connect_mqtt() #Might raise MQTTException
            except MQTTException as e:
                #TODO: need to figure out what else goes here
                print("Couldn't connect to MQTT Broker.")
                #Log inability to connect to MQTT
                state.log_file.error(f"Unable to start MQTT.")
                if state.internet_manager.is_connected():
                    time.sleep(2)
                    continue
                else:
                    break
            else:
                state.client.publish(state.topics["topic_log"], b'Hello from JMD_1000!')

                # Send the firmware version immediately on boot/reconnect and retain it
                state.client.publish(state.topics["topic_version"], f"{state.FW_REV}".encode(), retain=True, qos=1)

                #Make sure we start by clearing any alerts
                state.client.publish(state.topics["topic_alert"], b"CLEAR", retain=False, qos=0)
                state.client.subscribe(state.topics["topic_cmd_all"])
                break
        break

#Send start up notification
state.log_file.info(f"Detector started. Firmware: {state.FW_REV}.")
notify.send_inactivity_alert(f"Motion detector started at {state.myConfig.config['location']}. Firmware: {state.FW_REV}.")

#Enter the main endless while loop
while True:
    #Get the location specific time from the internet
    #Set up the real time clock (RTC)
    init_RTC(state.secrets) #Gets local time from the internet, requires internet connection
    termMsg(state.topics["topic_log"], f"Time updated from Internet at {state.rtc_manager.get_formatted_time()}.")
    #Start by assuming I just saw motion

    if state.rtc_manager.get_time_part("year") == 2000: #getting time from internet failed.
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

        #I can't do this earlier because I just initialized the RTC
        motion_time = time.time()

        #Second sub-loop
        while True:

            time.sleep(1) #1 second delay
            #Toggle the working led
            state.leds["Working"].toggle()

            #Check if there's any requests from the MQTT broker
            try:
                if state.client != None:
                    state.client.check_msg()
                else:
                    print("MQTT client was None, reconnecting...")
                    state.client = connect_mqtt()
            except (OSError, MQTTException) as e:
                print(f"MQTT error detected ({e}), checking WiFi before MQTT reconnection...")
                try:
                    state.client.disconnect()
                except:
                    pass  # Socket is already dead, ignore failure to disconnect cleanly
                # Check WiFi first — MQTT can't reconnect if WiFi is down
                if not state.internet_manager.is_connected():
                    print("WiFi lost — attempting WiFi reconnection first...")
                    state.leds["WIFI"].off()
                    try:
                        state.internet_manager.connect()
                    except Exception as wifi_err:
                        print(f"WiFi reconnection failed ({wifi_err}), will retry next cycle")
                        time.sleep(5)
                        continue  # skip MQTT reconnect, try again next loop iteration
                time.sleep(2)
                state.client = connect_mqtt()

            # Send a keep-alive ping every 60 seconds to prevent broker
            # dropping the subscription on long-running sessions
            if time.time() % 60 == 0:
                try:
                    state.client.ping()
                except:
                    pass


            #Send the current time (every 5 seconds) to the MQTT broker
            if time.time() % 5 == 0:
                try:
                    publish_time() #Only send the time every 5 seconds
                except OSError as e:
                    print(f"publish_time failed ({e}), will retry next cycle")

            detector_state = motion_detector_pin.value()	# Check Motion detector
            state.leds["Motion"].value(detector_state)

            if detector_state: #True - Motion was detected.
                motion_time = time.time() #The time when motion was last detected
                motion_flag = True
                ever_seen_motion = True
                alert_flag = False

                #Turn off alert (if active)
                if alert_condition:
                    alert_condition = False
                    state.leds["ALERT"].off()
                    # Need to send alert cancel message
                    state.log_file.info("ALERT cleared!")
                    alert_message_text = f"At {state.rtc_manager.seconds_to_time(time.time())} motion was detected at {state.myConfig.config['location']}, alert cancelled."
                    termMsg(state.topics["topic_log"], alert_message_text)
                    notify.send_inactivity_alert(alert_message_text)
                    try:
                        state.client.publish(state.topics["topic_alert"], b"CLEAR", retain=False, qos=0)
                    except OSError as e:
                        print(f"Failed to publish CLEAR alert state: {e}")
                break #Return to first sub-loop

            else: #No motion at the moment
                quiet_time = time.time() - motion_time
                alert_condition = (
                    ever_seen_motion        # don't alert before we've seen any motion at all
                    and (not motion_flag)   # no motion in the current check cycle
                    and (quiet_time >= state.myConfig.config["thresholds"]["inactivity_alert"])
                    and state.rtc_manager.is_in_awake_window(state.myConfig.config["awake_window"])
                )

                if alert_condition and not alert_flag: # no motion for at least the alert_time, Notify!
                    alert_flag = True
                    state.leds["ALERT"].on()
                    # Need to send alert active message
                    state.log_file.info("ALERT Triggered!")

                    # 1. Get the total seconds
                    total_seconds = state.myConfig.config["thresholds"]["inactivity_alert"]

                    # 2. Calculate hours, minutes, and seconds
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60

                    # 3. Format into HH:MM:SS
                    time_string = "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

                    alert_message_text = f"At {state.rtc_manager.seconds_to_time(time.time())} no motion has been detected for {time_string} at {state.myConfig.config['location']}."
                    termMsg(state.topics["topic_log"], alert_message_text)
                    notify.send_inactivity_alert(alert_message_text)
                    try:
                        state.client.publish(state.topics["topic_alert"], b"SET", retain=False, qos=0)
                    except OSError as e:
                        print(f"Failed to publish SET alert state: {e}")

                motion_flag = False
                continue #Next iteration of second sub-loop
