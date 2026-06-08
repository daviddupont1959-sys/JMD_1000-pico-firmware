'''
Motion Detector by Just Me Designs (JMD)
    This project is to create a motion detector which can send email messages to configured addresses in the event that no motion is detected after
     a configured time.
    It is meant to monitor that a person is actively moving in their environment, and alert loved ones if it appears they might have become unable to move
    It should be placed in a location that cannot detect a person if they have fallen in range of the detector,
     and could trigger it with arm movements or something like that.  The recommendation would be to plug it into a counter outlet in the kitchen area.
     The counter would block the detector from detecting the person on the floor even directly below the sensor.
    
    Two sets of email addresses can be configured
        A. Monitor email.  These addresses will receive an email daily to let the users know that the monitor is still operating.
        B. Alert email.  These addresses should be connfigured to generate text messages to the loved ones and will be sent when
           the configured timeout has occurred.  Most wireless carriers provide a special email address that will immediately send
           a test message to the subscriber.  For T-Mobile the address would look like this '6035983717@tmomail.net'
    
    2024/12/26 - Eliminate multi-threaded operation.
        Coding has been going on for a while, and I was trying to get the detecting code to run in a separate thread so that
        other things could be done in the main thread.  I came to the realization that there is nothing else that might need
        to be done once the device is configured.  Therefore I've decided to make a rather significant change to the code, and
        set it up to allow BlueTooth copnnections in the first 30 seconds after power on in order to allow for configuration,
        then enter into the monitor state, from which the only end is to remove power.
    
    2025/01/14 - Major restructure to clean up the code, and allow download of log files via bluetooth.
        Bluetooth connections are now allowed at any time.
        I might also provide for firmware updates over bluetooth. (This presents the problem of what to do if the update breaks the device.)
        
    2025/02/08 - Major development is complete.  Started work on making the code more robust especially with regards to internet connection stuff.
    
    2025/03/19 - added code to track and indicate firmware revision. Version 1.00
    
    2025/03/25 - Version 1.01
                 Fix time update code so it only fetches time ONCE a week from the internet.  Previous version repeated fetch several times.
                 
    2025/03/29 - Version 1.02
                 Changed order of log file archives so that log_1.txt is most recent, and log_5.txt is oldest
    
    2025/04/02 - Version 1.03
                 Added TST bluetooth command to test email and text messaging to make sure it's configured correctly.
        
    2025/04/17 - Version 1.04
                    Made a change so that if the internet connection fails the device wuill retry every 5 minutes
                    To support this I moved the creastion of the internet class to a separate definition
                    Main code changes made around line #374, the original code was replaced with a call to this new def around line #327
                    That code is around line #260
    
    2025/04/21 - Version 1.05
                    Discovered that the update function needs work.  Also want to add a way to get the current version when connected using the app.
                    
    2025/05/13 - Version 1.06
                    Noticed that when internet connection doesn't work, and time gets set to 2000, it needs to retry every so often,  I'm thinking every 15 minutes?
                    Also noticed that weekly time update is happening on Monday when it was supposed to be on Sunday
                    
    2025/06/08 - Version 1.07
                    Trying to add enough log file debug messages to help get this working next time I get a chance to try it at Dad's house.
                    2025/07/27 - confirmed working, but text messages aren't being sent.
    
    2025/07/28 - Version 1.08
                    T-Mobile decided to stop supporting the email to SMS gateway.
                    Need to come up with a long term solution, but for now.....
                    Changed sending alerts from text to email on alert, and alert cancelled.
                    
'''
from iClk import RTCManager
from iNet import InternetManager
from iBLE import BLESimplePeripheral
from iCFG import ConfigManager
from iEmail import EmailSender
from iLogFile import LogManager

import machine
import bluetooth
import time
import sys
import os
import ujson as json
import gc

import secrets

FW_REV = 1.08

# Define the pins and names for the LEDs
leds = {
    "WIFI": machine.Pin(8, machine.Pin.OUT),
    "BlueTooth": machine.Pin(7, machine.Pin.OUT),
    "Motion": machine.Pin(6, machine.Pin.OUT),
    "ALERT": machine.Pin(5, machine.Pin.OUT),
    "Working": machine.Pin(9, machine.Pin.OUT)
}

#Get stuff from the secrets file.
api_key = secrets.dateTime['API_KEY']
host = secrets.dateTime['host']

#Bluetooth global variables
rxData = []
payload_len = 0
myData = ""
myDataStruct = ""

# Class variables
email_sender = None
internet_manager = None
log_file = None
myConfig = None
ble_manager = None

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

def panic_leds(sweepCount = 3):
    myList = ["Working",
#         "WIFI",
#         "BlueTooth",
        "Motion",
        "ALERT"
        ]
    #Start with all LEDS off
    for name in myList: leds[name].off()

    for i in range(sweepCount):
        #Then turn them on one at a time in sequence
        for name in myList:
            #print(name)
            leds[name].on()  # Turn the LED off
            time.sleep(.1)
            leds[name].off()
        for name in reversed(myList):
            #print(name)
            leds[name].on()  # Turn the LED off
            time.sleep(.1)
            leds[name].off()
        #time.sleep(.25)

def check_bt():
    global payload_len
    global rxData
    global myData
    global leds
    global log_file
    global myConfig
    global ble_manager
    global FW_REV

    
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
            leds["Working"].toggle()
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
                #I have all the data in a JSON string called msgText
                if myConfig.filename in os.listdir():
                    # Open the file in read mode
                    with open(myConfig.filename, 'w') as file:
                        file.write(msgText)
                #Now that the file was updated, reload the configuration
                myConfig.load_config()
                log_file.info("Configuration updated.")
                machine.reset()
                
            elif msgType == "GET":
                #Respond by sending the configuration file (config.txt)
                #The filename should be gotten from the cig manager class myConfig.filenam
                log_file.info("Configuration requested.")
                if myConfig.filename in os.listdir():
                    # Open the file in read mode
                    with open(myConfig.filename, 'r') as file:
                        # send the entire content of the file over bluetooth
                        tmpStr = file.read()
                        ble_manager.send(''.join(["GET",tmpStr]))

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
                    
            elif msgType == "UPD":
                #This is a mechanism for firmware updates
                '''
                The message text starts with the target file path
                then separated by a comma the rest of the file text.
                '''
                file_path, file_data = msgText.split(",", 1) #Split on ONLY the first comma
                temp_file_path = file_path + ".tmp"
                
                # The transfer worked. Save file_data to file_path
                print(f"Saving file: {temp_file_path}")
                log_file.debug(f"Saving file: {temp_file_path}")

                with open(temp_file_path, 'w') as file:
                    # Write the string data to the file
                    file.write(file_data)
                log_file.info("Update to file " + file_path + " received.")
#                os.remove(file_path) #Remove the old file
#                os.rename(temp_file_path, file_path) #Rename the temporary file.
                log_file.info("Firmware update ready.")
                #Send a response to let the user know I'm done
                print(f"File: {temp_file_path} written.")
                log_file.debug(f"File: {temp_file_path} written.")
                ble_manager.send(''.join(["UPD","File updated."]))
                
            elif msgType == "RST":
                machine.reset()
                
            elif msgType == "TST":
                #Send test messages to the identified group
                # Destination ends up in msgText
                sendAlert("Test","This is a test message from the JMD Motion detector.",msgText)
            
            elif msgType == "VER":
                #Return the firmware version
                ble_manager.send(f"VER{FW_REV}")
                print(f"Version: {FW_REV} sent.")
                log_file.debug(f"Version: {FW_REV} sent.")
                        
            print("Payload processed")
            log_file.debug("Payload processed.")
                
def sendAlert(subject, message_text, destination):
    global email_sender
    global internet_manager
    global log_file
    global myConfig

    destination = myConfig.config[destination]

    try:
        internet_manager.connect()
        email_sender.send_email(subject, message_text, destination)
    except Exception as e: # Print the error message
        print("An error occurred:", e)
        log_file.error(e)
        #Make the LED's do something noticable.
        panic_leds(sweepCount = 3)
    finally:
        internet_manager.disconnect()
    log_file.alert(message_text)

def CreateInternetClass(config_info, WIFI_led, log_file_class):
#     global myConfig
#     global led_pins
#     global log_file
    global rtc_init
    global internet_manager
    
    #Create an instance of InternetManager
    try:
        internet_manager = InternetManager(config_info.config["wifi_ssid"], config_info.config["wifi_password"], WIFI_led)
    # Need to handle the case where that didn't work as expected.
    except SystemExit as e:
        print("No internet connection")
        log_file.error("No internet connection.")
        rtc_init = [2000,2,3,8,35,0] # My birthday in the year 2000 (RP2 didn't like 1959!)
    else:
        #Getting time from internet returns a time adjusted for time zone and daylight savings.
        print(f"Getting time (main) from{secrets.dateTime["host"]}.")
        log_file.debug(f"Getting time (main) from{secrets.dateTime["host"]}.")
        rtc_init = internet_manager.time_from_internet(secrets.dateTime["host"], secrets.dateTime["API_KEY"])


def main():
    global email_sender
    global internet_manager
    global log_file
    global myConfig
    global ble_manager
    global FW_REV
    
    print(f"Firmware revision {FW_REV}")

    '''
    ################################
    Hardware setup
    ################################
    '''
    #Set up the motion detector
    motion_detector_pin = machine.Pin(1, machine.Pin.IN, machine.Pin.PULL_DOWN)

    #Make sure all LEDs start in the off state
    for name, led in leds.items(): led.off()
    
    #Turn on status LED to indicate power (for now)
    leds["Working"].on()
    '''
    ################################
    Software Class setup
    ################################
    '''
    myConfig = ConfigManager()
    ''' Configuration needs to be loaded here because 
        other class setup calls require information
        from the configuration file.
    '''
    myConfig.load_config() # If the file doesn't exist, the default data is written

    # Set up to log information
    '''
        Supports 4 types of messages in this order
            log_file.debug(message)
            log_file.info(message)
            log_file.alert(message)
            log_file.error(message)
    '''
    log_file = LogManager(level=myConfig.config["logLevel"], max_size=8192) # DEBUG is minimum level to log info
    #This can't be earlier because the logging hasn't been set up
    log_file.debug(f"Firmware Rev:{FW_REV}")

    #Create an instance of InternetManager
    CreateInternetClass(myConfig, leds["WIFI"], log_file)

    rtc_manager = RTCManager(rtc_init)# Internet class will return my birthday in 2000 if unable to connect
    print(f"Current local time: {rtc_manager.get_formatted_time()}")
    log_file.debug(f"Current local time: {rtc_manager.get_formatted_time()}")

    sensorStartupTime = time.time() + 10 # should be 60

    #Initialize Email sender
    email_sender = EmailSender("smtp.gmail.com", 465, secrets.eMail['address'], secrets.eMail['key'])
    
    # Set up Bluetooth Low Energy for initial configuration:
    ble = bluetooth.BLE()
    ble_manager = BLESimplePeripheral(ble, led_pin=leds["BlueTooth"]) #This init starts advertising
     
    ble_manager.on_write(on_rx)

    #Send start up email notification
    log_file.info(f"Detector started. Firmware: {FW_REV}.")

    # First test if internet_manager was intitalized.
    if internet_manager != None:
        #If internet_manager was intialized, send an email indicating startup.
        strTime = rtc_manager.seconds_to_time(time.time())
        strBody = f"At {strTime} The motion detector was started at {myConfig.config["location"]} running firmware revision {FW_REV}."
        sendAlert("Motion detector started",strBody, "email_addresses")
    
    #Start by assuming I'm done sleeping and there's motion just stopped.
    sleep_until = motion_stopped = time.time()  # This will force no sleep at startup
    # Interval time and alert time should be in seconds at this level.
    tempStr = f"Interval: {myConfig.config["interval_time"]}, Alert: {myConfig.config["alert_time"]}"
    print(tempStr)
    log_file.info(tempStr)
    m_flag = a_flag = False

    #The motion sensor is documented to take 1 minute to start up
    print("Waiting for sensor to warm up.")
    while time.time() < sensorStartupTime:
        panic_leds(sweepCount = 1)

    while True: #This is the endless loop that does the actual monitoring.
        time.sleep(1) #Every one second...
        
        #If initializing the internet class failed, blink the WIFI LED
        if internet_manager == None:
            leds["WIFI"].toggle()
            leds["Working"].toggle()
            # If the minute counter MOD 5 == 0, try to get the time again
            # This is in case the router was not operational the last time we tried to get the time.
            if rtc_manager.get_time_part('minute') % 5 == 0 and rtc_manager.get_time_part("second") == 0:
                CreateInternetClass(myConfig, leds["WIFI"], log_file)
                rtc_manager = RTCManager(rtc_init)# Internet class will return my birthday in 2000 if unable to connect
        elif time.time() % 2 == 0:
            #Toggle the working indicator once every 2 seconds
            leds["Working"].toggle()
    
        detector_state = motion_detector_pin.value()	# Check Motion detector
        leds["Motion"].value(detector_state)

        check_bt() #See if a bluetooth connection was made.
        
        #Get time from internet every Sunday at 2:01 AM, in case Daylight Savings changed.
        #Apparently weekday 0 = Monday.  Changed this from 0 to 6.
        SundayMorning = rtc_manager.check_time(6,2,1) and rtc_manager.time_Updated[2] != rtc_manager.get_time_part("date")
        #This is here in case the most recent update returned the year 2000 which indicates internet connection didn't work.
        NeedUpdate = rtc_manager.get_time_part("year") == 2000 and rtc_manager.get_time_part("minute") % 15 == 0 and rtc_manager.get_time_part("second") % 60 == 0
        
        if SundayMorning or NeedUpdate:
            rtc_manager.setDateTime(internet_manager.time_from_internet(secrets.dateTime["host"], secrets.dateTime["API_KEY"]))
            #This should send a weekly email indicating time was updated
            sendAlert("Time updated", f"Time updated from Internet at {rtc_manager.get_formatted_time()}.", 'email_addresses')

    
        #If I'm done sleeping, decide what to do based on the motion detector
        if time.time() >= sleep_until: # If I'm done sleeping...
            # If motion was detected, stop checking for the interval time.
            if detector_state == 1:
                sleep_until = time.time() + myConfig.config["interval_time"]
                #Assume motion stops right away
                motion_stopped = time.time()
#                print("Sleeping until ",rtc_manager.seconds_to_time(sleep_until))
#                log_file.debug(f"Motion detected, sleeping until {rtc_manager.seconds_to_time(sleep_until)}")

            if detector_state == 0 and m_flag == True: # Motion stopped
                motion_stopped = time.time()
#                print(f"Motion stopped at {rtc_manager.seconds_to_time(motion_stopped)}")
#                log_file.debug(f"Motion stopped at {rtc_manager.seconds_to_time(motion_stopped)}")
            
            if detector_state == 1 and a_flag == True: # Motion detected but alert was previously sent
                # Need to send alert cancel message
                m_flag = True
                a_flag = False
                leds["ALERT"].off()
                print("ALERT cleared!")
                log_file.info("ALERT cleared!")
                #Send ALERT email notification
                email_message_text = f"At {rtc_manager.seconds_to_time(time.time())} motion was detected at {myConfig.config["location"]}, alert cancelled."
#                sendAlert("Motion ALERT CANCELLED", email_message_text, 'phone_numbers')
                sendAlert("Motion ALERT CANCELLED", email_message_text, 'email_addresses')

            #########################################
            # DEBUG ONLY
            #########################################
#             if detector_state == 0 and m_flag == False:# Motion stopped
#                 etime = time.time() - motion_stopped
#                 print(f"No motion for {etime} seconds.")
                
            alert_condition = m_flag == False and time.time() - motion_stopped >= myConfig.config["alert_time"]
            if alert_condition and a_flag == False: # no motion for at least the alert_time, Notify!
                a_flag = True
                leds["ALERT"].on()
                # Need to send alert active message
                print("ALERT Triggered!")
                log_file.info("ALERT Triggered!")
                #Send ALERT email notification
                email_message_text = f"At {rtc_manager.seconds_to_time(time.time())} no motion has been detected for {myConfig.config["alert_time"] / 3600} hours at {myConfig.config["location"]}."
#                sendAlert("Motion ALERT ISSUED!", email_message_text, 'phone_numbers')
                sendAlert("Motion ALERT ISSUED!", email_message_text, 'email_addresses')

        m_flag = detector_state #Make note of current detector state

        
main()