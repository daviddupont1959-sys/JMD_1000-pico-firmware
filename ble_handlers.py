"""
ble_handlers.py - Bluetooth LE command handling (DIR/FIL/PUT/GET/RST).
These mirror the equivalent MQTT commands in mqtt_handlers.py, used as a
local fallback/setup path when the device has no WiFi/MQTT connectivity
yet (e.g. initial WiFi credential provisioning).
"""
import time
import os
import gc
import machine
import ujson as json
import secure_config
import state

#Bluetooth receive-buffer state - local to this module since only
#on_rx() and check_bt() touch it.
rxData = []
payload_len = 0
myData = ""

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

    working_led = state.leds["Working"]

    payloadReceived = False
    blink_interval = 250 #In milliseconds
    last_blink_time = time.ticks_ms()

    while state.ble_manager.is_connected():
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
            state.log_file.debug(tmpStr)
            payloadReceived = False #Make sure I only do this once per payload

            if msgType =="PUT":
                #respond by saving SSID and Password (encrypted)
                #msgText will contain the JSON string for the ssid and password
                #First put the values in the internet manager class

                # Convert JSON to Python dictionary
                state.WiFi_Creds = json.loads(msgText)

                # Dynamically set attributes based on JSON keys
                for key, value in state.WiFi_Creds.items():
                    setattr(state.internet_manager, key, value)

                #Need to save this stuff to the encrypted file.
                #def save_config(filename: str, config: dict):
                secure_config.save_config("wifi.enc", state.WiFi_Creds)

                #Log this activity to the log file.
                state.log_file.info("WiFi Credentials updated.")

            elif msgType == "GET":
                #Respond by sending the SSID and Password which are in a dict called WiFi_Creds
                state.log_file.info("Configuration requested.")
                # send the WiFi credentials
                state.ble_manager.send(''.join(["GET",json.dumps(state.WiFi_Creds)]))

            elif msgType == "DIR":
                #Respond with list of log files.
                # List all files in the current directory
                files = os.listdir()

                # Filter out files that start with "log"
                log_files = [file for file in files if file.startswith('log')]
                # Convert the list to a JSON string
                state.ble_manager.send("DIR" + json.dumps(log_files))

            elif msgType == "FIL":
                #Respond with requested file
                # msgText contains the filename
                # Open the file in read mode
                with open(msgText, 'r') as file:
                    # send the entire content of the file over bluetooth
                    tmpStr = file.read()
                    state.ble_manager.send(''.join(["FIL",tmpStr]))
                    tmpStr = f"Log file {msgText} sent."
                    state.log_file.info(tmpStr)

            elif msgType == "RST":
                machine.reset()

def allow_bt(ble_interval = 15):
    #Wait a while for a bluetooth connection.
    state.ble_manager.advertise(interval_us = ble_interval * 1000)
    bleStartTime = time.time()
    while time.time() - bleStartTime < ble_interval: #We'll have to see if I like <ble_interval> seconds or not.
        #Bluetooth LED is handled by the class, and turns on when a connection is made
        #If there's a connection take care of it
        if state.ble_manager.is_connected():
            state.ble_manager.led_control("on")  # ensure BLE LED is on
            check_bt()
        else:
            time.sleep(0.25)
            state.ble_manager.led_control("toggle")
    # If we got here the timer is done, make sure led is off, the stop advertising
    state.ble_manager.led_control("off")
    state.ble_manager.un_advertise()
