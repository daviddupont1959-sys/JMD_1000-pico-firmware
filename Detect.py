''' Motion Detector Firmware
11/02/2025 - Start of complete re-write
                This code will be based on a Miro flow diagram
                https://miro.com/app/board/uXjVJDQWxEE=/
            Of course copying libraries and functions where it makes sense.
'''

# Includes
from iClk import RTCManager
from iNet import InternetTimeoutError
from iNet import InternetManager
from iBLE import BLESimplePeripheral
from iCFG import ConfigManager
from iLogFile import LogManager
from umqtt_simple import MQTTClient
import secure_config

from umqtt_simple import MQTTException

import machine
import bluetooth
import time
import sys
import os
import ujson as json
import gc
import urequests
import ota_update

'''
# Variable Definition
'''
FW_REV = "2.04"
rtc_value = [2000,2,3,8,35,0] # My birthday in the year 2000 (RP2 didn't like 1959!)

# Class variables
internet_manager = None
ble_manager = None
log_file = None
myConfig = None

#Bluetooth global variables
rxData = []
payload_len = 0
myData = ""
myDataStruct = ""

# 1. Define your secure, secret ntfy URL
NTFY_URL = "https://ntfy.sh/jmd_1000_safety_alert_4784"


#MQTT Variables
client = None
mqtt_server = 'broker.emqx.io'
#client_id = 'devices/JMD_1000'
client_id = 'JMD_1000'
#MQTT Topics
topics = {
    # ----------------------------------------------------
    # STATE + ALERT OUTPUT
    # ----------------------------------------------------
    "topic_motion"         : f"{client_id}/state/motion".encode(),
    "topic_last_motion"    : f"{client_id}/state/last_motion".encode(),
    "topic_alert"          : f"{client_id}/alert/inactivity".encode(),
    "topic_test_alert"     : f"{client_id}/alert/test".encode(),
    "topic_time"           : f"{client_id}/state/time".encode(),
    "topic_awake"          : f"{client_id}/state/awake".encode(),
    "topic_version"        : f"{client_id}/state/version".encode(),
    
    # ----------------------------------------------------
    # CONFIG PUBLISHING
    # ----------------------------------------------------
    "topic_cfg_full"       : f"{client_id}/config/full".encode(),

    # ----------------------------------------------------
    # COMMANDS (phones → device)
    # ----------------------------------------------------
    "topic_set_config"       : f"{client_id}/command/set_config".encode(),
    "topic_test_cmd"         : f"{client_id}/command/test_alert".encode(),
    "topic_reboot"           : f"{client_id}/command/reboot".encode(),
    "topic_sync_time"        : f"{client_id}/command/sync_time".encode(),
    "topic_cfg_get"          : f"{client_id}/command/get_config".encode(),
    "topic_update"          : f"{client_id}/command/update".encode(),

    # ----------------------------------------------------
    # RESPONSES (device → phones)
    # ----------------------------------------------------
    "topic_ack"            : f"{client_id}/response/ack".encode(),
    "topic_err"            : f"{client_id}/response/error".encode(),
    "topic_log"            : f"{client_id}/response/log".encode(),

    # ----------------------------------------------------
    # WILDCARD SUBSCRIPTIONS
    # ----------------------------------------------------
    "topic_cmd_all"        : f"{client_id}/command/#".encode(),
}

# ----------------------------------------------------
# MQTT HANDLER FUNCTIONS
# ----------------------------------------------------

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
        trial = json.loads(json.dumps(myConfig.config))
        merge_dict(trial, update)

        myConfig.config = trial
        myConfig._sanitize()
        myConfig.save_config()

        client.publish(topics["topic_ack"], b"OK: config updated")
    except Exception as e:
        client.publish(topics["topic_err"], f"ERR: {e}".encode())

#This seems to be working. (I think I saw the alert go back to CLEAR once)
def handle_test_alert(msg_str):
    client.publish(topics["topic_alert"], b"TEST")
    client.publish(topics["topic_ack"], b"OK: test alert sent")
    send_inactivity_alert("Alert Testing Requested.")
    

#This seems to be working
def handle_reboot(msg_str):
    client.publish(topics["topic_ack"], b"OK: rebooting")
    time.sleep(1)
    machine.reset()


#This would require the phone to send the current timestamp as {"epoch": <unix_timestamp>}
def handle_sync_time(msg_str):
    try:
        update = json.loads(msg_str)
        epoch = update["epoch"]
        # Convert epoch to a datetime list [year, month, day, hour, minute, second]
        t = time.localtime(epoch)
        datetime_list = [t[0], t[1], t[2], t[3], t[4], t[5]]
        rtc_manager.setRTC(datetime_list)
        client.publish(topics["topic_ack"], b"OK: time synced")
    except Exception as e:
        client.publish(topics["topic_err"], f"ERR: time sync failed: {e}".encode())


#This seems to be working
def handle_cfg_get(msg_str):
    path = (msg_str or "").strip()

    # No path given -> return the full config (original behavior)
    if not path:
        cfg_json = myConfig.get_config()
        client.publish(topics["topic_cfg_full"], cfg_json.encode())
        return

    # Walk the dot-path, e.g. "thresholds.inactivity_alert"
    keys = path.split(".")
    node = myConfig.config
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            client.publish(
                topics["topic_err"],
                f"ERR: unknown config key '{path}'".encode()
            )
            return

    # Found it - report back the single value
    try:
        value_json = json.dumps({path: node})
    except Exception:
        value_json = json.dumps({path: str(node)})
    client.publish(topics["topic_cfg_full"], value_json.encode())

def handle_update(msg_str):
    result = ota_update.check_and_update()
    # Optionally publish the result back before rebooting
    client.publish(topics["topic_ack"], str(result).encode())
    # Commented for now so I can look at what is going on.
#     if result["updated"] and not result["errors"]:
#         machine.reset()

# Explicit top-down order, independent of dict iteration behavior
led_order = ["Working", "WIFI", "BlueTooth", "Motion", "ALERT"]

def lamp_test():
    for pin in leds.values():
        pin.off()
    for i in range(3):
        print("LED Test loop:", i + 1)
        for name in led_order:
            pin = leds[name]
            pin.on()
            time.sleep(.25)
            pin.off()

# ----------------------------------------------------
# COMMAND DISPATCHER
# ----------------------------------------------------
command_handlers = {
    topics["topic_set_config"]: handle_set_config,
    topics["topic_test_cmd"]: handle_test_alert,
    topics["topic_reboot"]: handle_reboot,
    topics["topic_sync_time"]: handle_sync_time,
    topics["topic_cfg_get"]: handle_cfg_get,
    topics["topic_update"]: handle_update,
}


# Define the pins and names for the LEDs
leds = {
    "Working": machine.Pin(20, machine.Pin.OUT),
    "WIFI": machine.Pin(19, machine.Pin.OUT),
    "BlueTooth": machine.Pin(18, machine.Pin.OUT),
    "Motion": machine.Pin(17, machine.Pin.OUT),
    "ALERT": machine.Pin(16, machine.Pin.OUT)
}

lamp_test()

#Start by assuming no motion or alert
detector_state = alert_state = False
#Make sure there's no motion or alert flags set.
motion_flag = alert_flag = False
alert_condition = False
# Tracks whether motion has been seen at least once since boot.
# Prevents a false alert if the device starts up during a quiet period.
ever_seen_motion = False

#This is loaded from an encrypted file.
secrets = secure_config.load_config("secrets.enc") # E-mail and geo-location creds.

'''
# Functions
'''


def send_inactivity_alert(message_text):
    """
    Sends a high-priority push notification directly to your phone 
    when the 8-hour inactivity threshold is crossed.
    """
    
    # 2. Set up the headers to format the notification on your phone
    headers = {
        "Title": "🚨 DAD'S HOUSE ALERT 🚨",
        "Priority": "high",       # 'high' or '5' ensures it stands out on your phone
        "Tags": "warning,house",   # Adds a warning emoji and house emoji automatically
    }
    
    # 3. Define the message payload text
#    message_text = f"No motion has been detected at Dad's house for {quiet_time} hours. Please check in."
    
    try:
        # Send the POST request to the ntfy cloud server
        response = urequests.post(NTFY_URL, data=message_text, headers=headers)
        
        # MicroPython Best Practice: Always check status and close socket connections!
        if response.status_code == 200:
            print("Push notification delivered to ntfy.sh successfully.")
        else:
            print(f"Server responded with an error code: {response.status_code}")
            
        response.close() 
        
    except Exception as e:
        # Catches network timeouts, temporary Wi-Fi drops, etc.
        print(f"Failed to transmit push notification: {e}")
        
def get_dict_value(base_dicts: dict, path: str):
    top = None
    if not path:# or "['" not in path:
        return None
    elif "['" not in path:
        top = path
    else:
        # Extract top-level name before the first [
        top = path.split("[", 1)[0]

    if top not in base_dicts:
        return None

    value = base_dicts[top]

    # Extract everything between [' and ']
    parts = []
    start = 0
    while True:
        i = path.find("['", start)
        if i == -1:
            break
        j = path.find("']", i)
        if j == -1:
            break
        key = path[i+2:j]
        parts.append(key)
        start = j + 2

    for k in parts:
        try:
            value = value[k]
        except (KeyError, TypeError):
            return None
    return value

def on_rx(v): #This is a bluetooth callback to handle incoming data
    global payload_len
    global rxData
    global myData
    
    if payload_len == 0:
        decoded_string = v.decode('utf-8')
        numeric_string = "".join(filter(str.isdigit, decoded_string))
        payload_len = int(numeric_string)
#        print(v, ":", payload_len)
         #Ensure these start empty
        rxData = []
        myData = ""
    else:
         rxData.append(v)
         print(rxData)

#Only call this if you detect a BT connection,
# it will run until the connection is closed
def check_bt():
    #The class handles the bluetooth LED
    global payload_len
    global rxData
    global myData
    global log_file
    global ble_manager
    global FW_REV
    global leds
    global WiFi_Creds
    
    working_led = leds["Working"]
    
    payloadReceived = False
    blink_interval = 250 #In milliseconds
    last_blink_time = time.ticks_ms()
    
    while ble_manager.is_connected():
        #Toggle the working indicator once every <blink_interval> seconds
        #Get the current time
        current_time = time.ticks_ms()
        #Check if it's time to toggle the LED
        if time.ticks_diff(current_time, last_blink_time) >= blink_interval:
            #Toggle the LED
            working_led.toggle()
            #Update the last blink time.
            last_blink_time = current_time

        if payload_len != 0 and len(rxData) != 0: #There's some payload to process
            gc.collect()
            
            packetStr = rxData.pop(0).decode("utf-8").strip('\00')
            payload_len -= len(packetStr) # Decrement by the string length of the first list element.
            if payload_len < 0: payload_len = 0
            myData += packetStr
            payloadReceived = payload_len == 0 # True if the WHOLE payload has been received

        if payloadReceived:
            msgType = myData[:3]
            msgText = myData[3:]
            #DEBUG
            tmpStr = "Type: " + msgType + " Message: " + msgText[:32] #Only show the first 32 characters of the message
            print(tmpStr)
            log_file.debug(tmpStr)
            payloadReceived = False #Make sure I only do this once per payload
            
            if msgType =="PUT":
                #respond by saving SSID and Password (encrypted)
                #msgText will contain the JSON string for the ssid and password
                #First put the values in the internet manager class

                # Convert JSON to Python dictionary
                WiFi_Creds = json.loads(msgText)

                # Dynamically set attributes based on JSON keys
                for key, value in WiFi_Creds.items():
                    setattr(internet_manager, key, value)
                    
                #Need to save this stuff to the encrypted file.
                #def save_config(filename: str, config: dict):
                secure_config.save_config("wifi.enc", WiFi_Creds)
                
                #Log this activity to the log file.
                log_file.info("WiFi Credentials updated.")
                
            elif msgType == "GET":
                #Respond by sending the SSID and Password which are in a dict called WiFi_Creds
                log_file.info("Configuration requested.")
                # send the WiFi credentials
                # internet_manager_dict = {"ssid": internet_manager.ssid, "password": internet_manager.password}
                # print(internet_manager_dict)
                # WAS - ble_manager.send(''.join(["GET",json.dumps(internet_manager_dict)]))
                ble_manager.send(''.join(["GET",json.dumps(WiFi_Creds)]))

            elif msgType == "DIR":
                #Respond with list of log files.
                # List all files in the current directory
                files = os.listdir()

                # Filter out files that start with "log"
                log_files = [file for file in files if file.startswith('log')]
                # Convert the list to a JSON string
                ble_manager.send("DIR" + json.dumps(log_files))

            elif msgType == "FIL":
                #Respond with requested file
                # msgText contains the filename
                # Open the file in read mode
                with open(msgText, 'r') as file:
                    # send the entire content of the file over bluetooth
                    tmpStr = file.read()
                    ble_manager.send(''.join(["FIL",tmpStr]))
                    tmpStr = f"Log file {msgText} sent."
                    log_file.info(tmpStr)
                    
            elif msgType == "RST":
                machine.reset()
                
def termMsg(topic, message_text):
    #These are now going to the MQTT broker
    global client
    global log_file
#    global myConfig

    print(message_text)
    #Need to add code to send alerts to the MQTT broker
    if client != None:
        try:
            if isinstance(message_text, str):
                message_text = message_text.encode()

            client.publish(topic, message_text)
        except (SystemExit, OSError) as e:
            print(f"Trouble publishing: {message_text} to {topic}: {e}")
            log_file.error(f"Trouble publishing: {message_text} to {topic}: {e}")

def init_RTC(secrets):
    #Expects that internet is connected.
    global internet_manager
    
    #Getting time from internet returns a time adjusted for time zone and daylight savings.
    #If there's not internet connection the return value will be my birthday in the year 2000
    termMsg(topics["topic_log"], f"Getting time (main) from{secrets['dateTime']['host']}.")
    log_file.debug(f"Getting time (main) from{secrets['dateTime']['host']}.")
    # Wrap in try/except — time_from_internet can throw OSError -2 (DNS failure)
    # even when WiFi and MQTT are connected. In that case we fall back to the
    # birthday sentinel (year 2000) so the main loop knows to retry later.
    try:
        rtc_manager.setRTC(internet_manager.time_from_internet(secrets["dateTime"]["host"], secrets["dateTime"]["API_KEY"]))
    except OSError as e:
        log_file.error(f"init_RTC: DNS/network error getting time: {e}. Will retry in main loop.")
        termMsg(topics["topic_log"], f"init_RTC: time sync failed (OSError {e}), will retry.")
        # Leave RTC at birthday sentinel — main loop NeedUpdate logic will retry
        
    return #No value is returned, but the RTC value (inside that class) will be updated.
 
def allow_bt(ble_interval = 15):
    global ble_manager
    #Wait a while for a bluetooth connection.
    ble_manager.advertise(interval_us = ble_interval * 1000)
    bleStartTime = time.time()
    while time.time() - bleStartTime < ble_interval: #We'll have to see if I like <ble_interval> seconds or not.
        #Bluetooth LED is handled by the class, and turns on when a connection is made
        #If there's a connection take care of it
        if ble_manager.is_connected():
            ble_manager.led_control("on")  # ensure BLE LED is on
            check_bt()
        else:
            time.sleep(0.25)
            ble_manager.led_control("toggle")
    # If we got here the timer is done, make sure led is off, the stop advertising
    ble_manager.led_control("off")
    ble_manager.un_advertise()

def mqtt_callback(topic, msg):
    try:
        msg_str = msg.decode()
    except:
        print("MQTT decode error")
        return

    handler = command_handlers.get(topic)

    if handler:
        handler(msg_str)
    else:
        print("Unknown topic:", topic)
        client.publish(topics["topic_err"], b"ERR: unknown topic")
                   
import time
import sys

# Assuming you are using umqtt.simple or umqtt.robust
# If your library has a specific MQTTException, import it:
# from umqtt.simple import MQTTException 

def connect_mqtt(retries=5, delay=2):
    client = MQTTClient(
        client_id, 
        mqtt_server, 
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
            res = client.connect(timeout=5)
            
            if res == 0 or res is None: 
                print(f'Successfully connected to {mqtt_server} as {client_id}')
                client.subscribe(topics["topic_cmd_all"])
                print(f'Subscribed to {topics["topic_cmd_all"]}')
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

# def connect_mqtt():
#     client = MQTTClient(client_id, mqtt_server, user='PicoMan', password='Pico2Stuff', port=1883, ssl=False, keepalive = 60)
# 
#     client.set_callback(mqtt_callback)
#     try:
#         client.connect(timeout = 5)
#     except:
#         print("MQTT Connection timed out!")
#     else:
#         print(f'Connected to {client_id}')
#     return client

def publish_time():
    global client
    global topics
    
    # Get the current Unix timestamp
    current_seconds = time.time()

    # Convert to a local time tuple
    # The tuple contains: (year, month, mday, hour, minute, second, weekday, yearday)
    time_tuple = time.localtime(current_seconds)

    # Extract individual components for easier formatting
    year, month, day, hour, minute, second, weekday, yearday = time_tuple

    # Convert to a local time tuple
    # The tuple contains: (year, month, mday, hour, minute, second, weekday, yearday)
    time_tuple = time.localtime(current_seconds)
    # Example 1: Basic YYYY-MM-DD HH:MM:SS format
    formatted_time_basic = "{:02d}:{:02d}:{:02d}".format(hour, minute, second)
    
    termMsg(topics["topic_time"], f"publishing: {formatted_time_basic} to {topics['topic_time']}")
    # 1. Get the True/False value for awake state and convert it to text
    awake_payload = str(rtc_manager.is_in_awake_window(myConfig.config["awake_window"]))

    try:
        client.publish(topics["topic_time"], formatted_time_basic.encode())
        client.publish(topics["topic_awake"], awake_payload)
    except OSError as e:
        print(f"publish_time failed ({e}), checking WiFi...")
        # Check WiFi first — MQTT reconnect is pointless if WiFi is down
        if not internet_manager.is_connected():
            print("WiFi lost — attempting reconnection...")
            leds["WIFI"].off()
            try:
                internet_manager.connect()
            except Exception as wifi_err:
                print(f"WiFi reconnection failed: {wifi_err}")
                return  # Give up this cycle, retry next time
        # WiFi is up (or just reconnected) — reconnect MQTT
        try:
            client.disconnect()
        except:
            pass
        client = connect_mqtt()



'''
# INIT Classes
#   Configuration manager
#   Log File manager
#   Internet manager
#   Real Time Clock (RTC) manager (requires internet)
#   Bluetooth handler
'''
#   Configuration manager
myConfig = ConfigManager()
''' Configuration needs to be loaded first because... 
    other class setup calls require information
    from the configuration file.
'''
myConfig.load_config() # If the file doesn't exist, the default data is written

#   Log File manager
# Set up to log information
'''
Supports 4 types of messages in this order
        log_file.debug(message)
        log_file.info(message)
        log_file.alert(message)
        log_file.error(message)
'''
log_file = LogManager(level=myConfig.config["log_level"], max_size=2048)

#   Internet manager
#Get wifi connection info from encrypted file.
#It's a dictionary with two entries: ssid, and password
WiFi_Creds = secure_config.load_config("wifi.enc")
internet_manager = InternetManager(WiFi_Creds["ssid"], WiFi_Creds["password"], leds["WIFI"])
#Force a disconnect in case there's a leftovcer connetcion
internet_manager.disconnect()

#   Real Time Clock (RTC) manager (requires internet)
rtc_manager = RTCManager()
    
#   Bluetooth handler
ble_manager = BLESimplePeripheral(bluetooth.BLE(), led_pin=leds["BlueTooth"], name="Motion")
# Define the call back routine for incoming data.
ble_manager.on_write(on_rx)


'''
# Hardware INIT
'''

#Set up the motion detector
motion_detector_pin = machine.Pin(28, machine.Pin.IN, machine.Pin.PULL_DOWN)

#Make sure all LEDs start in the off state
for name, led in leds.items(): led.off()

'''
# Main code
'''
# Turn on Working LED
leds["Working"].on()

#Allow for a bluetooth connection for 15 seconds
allow_bt(ble_interval = 15)
#Connect to internet and MQTT broker
while True: #This acts like a repeat / until loop
    #repeat
    try:
        internet_manager.connect() #See if internet is connected
    except InternetTimeoutError:
        allow_bt(ble_interval = 15) #Otherwise, allow bluetooth connection (potentially modifying wifi creds)
        continue #Which will try to connect again
    else: #Connection must have worked so... Connect to the MQTT Broker
        while True:
            try:
                client = connect_mqtt() #Might raise MQTTException
            except MQTTException as e:
                #TODO: need to figure out what else goes here
                print("Couldn't connect to MQTT Broker.")
                #Log inability to connect to MQTT
                log_file.error(f"Unable to start MQTT.")
                if internet_manager.is_connected():
                    time.sleep(2)
                    continue
                else:
                    break
            else:
                client.publish(topics["topic_log"], b'Hello from JMD_1000!')
                
                # Send the firmware version immediately on boot/reconnect and retain it
                client.publish(topics["topic_version"], f"{FW_REV}".encode(), retain=True, qos=1)
                
                #Make sure we start by clearing any alerts
                client.publish(topics["topic_alert"], b"CLEAR", retain=False, qos=0)
                client.subscribe(topics["topic_cmd_all"])
                break
        break

#Send start up notification
log_file.info(f"Detector started. Firmware: {FW_REV}.")
send_inactivity_alert(f"Motion detector started at {myConfig.config['location']}. Firmware: {FW_REV}.")

#Enter the main endless while loop
while True:
    #Get the location specific time from the internet
    #Set up the real time clock (RTC)
    init_RTC(secrets) #Gets local time from the internet, requires internet connection
    termMsg(topics["topic_log"], f"Time updated from Internet at {rtc_manager.get_formatted_time()}.")
    #Start by assuming I just saw motion

    if rtc_manager.get_time_part("year") == 2000: #getting time from internet failed.
        termMsg(topics["topic_log"], "Getting time from internet failed. Waiting 1 minute")
        time.sleep(60)  #Wait for 1 minute
        continue        #Try again

    #First sub-loop
    while True: #Get time from internet every Sunday at 2:01 AM, in case Daylight Savings changed.
        #Apparently weekday 0 = Monday, therefore Sunday is 6.
        #The AND part should prevent updating more than once a day
        #When for example the clocks go back to standard time
        SundayMorning = rtc_manager.check_time(6,2,1) and rtc_manager.time_Updated[2] != rtc_manager.get_time_part("date")
        #This is here in case the most recent update returned the year 2000 which indicates internet connection didn't work.
        NeedUpdate = rtc_manager.get_time_part("year") == 2000 and rtc_manager.get_time_part("minute") % 15 == 0 and rtc_manager.get_time_part("second") % 60 == 0
        
        if SundayMorning or NeedUpdate:
            break #Exits current loop and causes parent loop to go to next iteration. Which starts by getting the time.
            
        #I can't do this earlier because I just initialized the RTC
        #Uses ticks_ms (a free-running counter) instead of time.time() because
        #time.time() is tied to the RTC, and init_RTC() can jump the RTC forward
        #or backward (weekly resync, DST, or recovering from a lost internet
        #connection). ticks_ms() is unaffected by RTC changes, so quiet_time
        #below always reflects real elapsed seconds.
        motion_time = time.ticks_ms()
        
        #Second sub-loop
        while True:
        
            time.sleep(1) #1 second delay
            #Toggle the working led
            leds["Working"].toggle()

            #Check if there's any requests from the MQTT broker
            try:
                if client != None:
                    client.check_msg()
                else:
                    print("MQTT client was None, reconnecting...")
                    client = connect_mqtt()
            except (OSError, MQTTException) as e:
                print(f"MQTT error detected ({e}), checking WiFi before MQTT reconnection...")
                try:
                    client.disconnect()
                except:
                    pass  # Socket is already dead, ignore failure to disconnect cleanly
                # Check WiFi first — MQTT can't reconnect if WiFi is down
                if not internet_manager.is_connected():
                    print("WiFi lost — attempting WiFi reconnection first...")
                    leds["WIFI"].off()
                    try:
                        internet_manager.connect()
                    except Exception as wifi_err:
                        print(f"WiFi reconnection failed ({wifi_err}), will retry next cycle")
                        time.sleep(5)
                        continue  # skip MQTT reconnect, try again next loop iteration
                time.sleep(2)
                client = connect_mqtt()

            # Send a keep-alive ping every 60 seconds to prevent broker
            # dropping the subscription on long-running sessions
            if time.time() % 60 == 0:
                try:
                    client.ping()
                except:
                    pass
    
                
            #Send the current time (every 5 seconds) to the MQTT broker
            if time.time() % 5 == 0:
                try:
                    publish_time() #Only send the time every 5 seconds
                except OSError as e:
                    print(f"publish_time failed ({e}), will retry next cycle")

            detector_state = motion_detector_pin.value()	# Check Motion detector
            leds["Motion"].value(detector_state)
            
            if detector_state: #True - Motion was detected.
                motion_time = time.ticks_ms() #The time when motion was last detected (ticks_ms, not RTC-based)
                motion_flag = True
                ever_seen_motion = True

                #Capture the latch BEFORE resetting it. alert_flag (not alert_condition)
                #is the correct thing to check here: alert_condition is recomputed live
                #every loop cycle (including the awake-window term), so it can silently
                #flip back to False on its own (e.g. awake window changes) with no motion
                #involved. Checking alert_condition here meant that once it had flipped
                #False on its own, this block would never run on the next real motion
                #event -- leaving the alert LED stuck on and no CLEAR message ever sent.
                was_alert_active = alert_flag
                alert_flag = False
                alert_condition = False

                #Turn off alert (if one was actually active)
                if was_alert_active:
                    leds["ALERT"].off()
                    # Need to send alert cancel message
                    log_file.info("ALERT cleared!")
                    alert_message_text = f"At {rtc_manager.seconds_to_time(time.time())} motion was detected at {myConfig.config['location']}, alert cancelled."
                    termMsg(topics["topic_log"], alert_message_text)
                    send_inactivity_alert(alert_message_text)
                    try:
                        client.publish(topics["topic_alert"], b"CLEAR", retain=False, qos=0)
                    except OSError as e:
                        print(f"Failed to publish CLEAR alert state: {e}")
                break #Return to first sub-loop
                            
            else: #No motion at the moment
                #ticks_diff correctly handles ticks_ms() wraparound; result is in ms,
                #so divide by 1000 to get seconds for comparison against the config threshold.
                quiet_time = time.ticks_diff(time.ticks_ms(), motion_time) / 1000
                alert_condition = (
                    ever_seen_motion        # don't alert before we've seen any motion at all
                    and (not motion_flag)   # no motion in the current check cycle
                    and (quiet_time >= myConfig.config["thresholds"]["inactivity_alert"])
                    and rtc_manager.is_in_awake_window(myConfig.config["awake_window"])
                )

                if alert_condition and not alert_flag: # no motion for at least the alert_time, Notify!
                    alert_flag = True
                    leds["ALERT"].on()
                    # Need to send alert active message
                    log_file.info("ALERT Triggered!")

                    # 1. Get the total seconds
                    total_seconds = myConfig.config["thresholds"]["inactivity_alert"]

                    # 2. Calculate hours, minutes, and seconds
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60

                    # 3. Format into HH:MM:SS
                    time_string = "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)
                    
                    alert_message_text = f"At {rtc_manager.seconds_to_time(time.time())} no motion has been detected for {time_string} at {myConfig.config['location']}."
                    termMsg(topics["topic_log"], alert_message_text)
                    send_inactivity_alert(alert_message_text)
                    try:
                        client.publish(topics["topic_alert"], b"SET", retain=False, qos=0)
                    except OSError as e:
                        print(f"Failed to publish SET alert state: {e}")
            
                motion_flag = False
                continue #Next iteration of second sub-loop
