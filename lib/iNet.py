import network
import socket
import time
import urequests
import ujson
import machine
import sys

class InternetTimeoutError(Exception):
    pass

class InternetNotConnected(Exception):
    pass
    
class InternetManager:
    def __init__(self, ssid, password, led_pin):
        self.ssid = ssid
        self.password = password
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self._wan_ip = ""
#        self.connect_flag = False
        self.led = led_pin
        self.led.off() #Make sure it starts in the off state
        #Placehoders
        self._dateTimeURL = ""
        #################################
        # DON'T FORGET!
        # Need to get WAN IP
        #################################
        
        # Code commented out because I don't believe it should be part of init   
        # #The Wide Area Network address (WAN-IP) can be used to determine time zone.
        # self._wan_ip = "172.56.93.182" #Will default to be in USA Central Time Zone
        # # This next code will get the router IP from the internet
        # try:
            # self.connect(seconds = 10)
        # except SystemExit as e:
            # sys.exit(1)
    
        # self.getRouterIP()
        # self.disconnect()
        # #print("INIT to: ", self.ssid, self.password)


    def connect(self, seconds = 5):
        network.hostname("JMD-1000")
        self.wlan.active(True)
        retry_time = seconds
        #Only try to connect if I'm not already connected.
        if not self.wlan.isconnected(): #self.connect_flag:
            for retry_count in range(1,4): #Maximum of 3 retries
                seconds = retry_time
                print(f"Connecting to {self.ssid}... try #{retry_count}.")
                try:
                    self.wlan.connect(self.ssid, self.password)
                except:
                    #If you got here, connection timed out after three retries
                    raise InternetNotConnected("Error connecting to internet")
                
                while not self.wlan.isconnected() and seconds > 0:
                    print(f"Waiting {seconds} seconds for connection...")
                    seconds -= 1
                    time.sleep(1)

                if seconds == 0: # If seconds == 0 we timed out.
                    self.led.off()
                    print(f"Internet connection try # {retry_count} timed out!")
                    time.sleep(2)
                    continue #Jump to next iteration of the for loop
                elif self.wlan.isconnected():
                    #Getting here implies I have a connection
                    self.led.on()
                    print("Success!")
                    return #Exit IMMEDIATELY to avoid raising the exception.
            raise InternetTimeoutError


    def disconnect(self):
        if self.wlan.isconnected():#self.connect_flag:
            self.wlan.disconnect()
            self.wlan.active(False)
            self.led.off()
            #self.connect_flag = False
            print("Disconnected from WIFI.")

    def is_connected(self):
        # wlan.isconnected() can return True even when the connection is broken.
        # wlan.status() == 3 (network.STAT_GOT_IP) is more reliable.
        return self.wlan.isconnected() and self.wlan.status() == 3

    def led_control(self, mode):
        if mode == "toggle": self.led.toggle()
        elif mode == "on": self.led.on()
        elif mode == "off": self.led.off()
        
    def _setDateTimeURL(self, host, api_key):
        #I need the router IP for this to work.
        if self._wan_ip == "": self.getRouterIP()
        # print(host,api_key,self._wan_ip)
        self._dateTimeURL = 'https://' + host + '?apiKey='+ api_key + '&ip=' + self._wan_ip
        
    def get_ip(self):
        if self.wlan.isconnected():
            return self.wlan.ifconfig()[0]
        else:
            return None
        
    def getRouterIP(self):
        """
        Get the public (WAN) IP address of the router by querying an external service.
        
        Returns:
            Nothing, but sets class variable self._wan_ip
            str: The public IP address as a string, or None if there is an error.
        """
        if self.wlan.isconnected() == False: self.connect
        response = None
        try:
            # Use an external service to get the WAN IP (public IP)
            response = urequests.get("http://api.ipify.org")
            
            if response.status_code == 200:
                # Return the IP address as a string
                self._wan_ip = response.text
                print(f"WAN (Public) IP Address: {self._wan_ip}")
                return
            else:
                print(f"Failed to get WAN IP, status code: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching WAN IP: {e}")
            return None
        finally:
            if response:
                response.close()
            
    def get_local_time_zone(self):
        #requires that _setDateTimeURL be called previously
        response = urequests.get(self._dateTimeURL)
        data = ujson.loads(response.text)
        response.close()

        return data['timezone_offset_with_dst']
    
    def getDateTime(self): #See comments!
        #Used for getting geo located time from the internet.
        #BUT... This only works if already connected to internet.
        # Use time_from_internet when calling from outside
        addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]

        s = socket.socket()
        s.bind(addr)
        s.listen(1)

        # Get current time, based on router IP address' location in the real world.
        timeJSON = urequests.get(self._dateTimeURL)
        #https://api.ipgeolocation.io/timezone?apiKey=4324de1bd6ac40d780b0d8c6fc8a1d20&ip=172.56.93.182
        
        ''' Example Response
        {
        "geo": {
            "country_code2": "US",
            "country_code3": "USA",
            "country_name": "United States",
            "country_name_official": "United States of America",
            "state_prov": "Texas",
            "state_code": "US-TX",
            "district": "",
            "city": "Austin",
            "zipcode": "78701",
            "latitude": "30.26759",
            "longitude": "-97.74299"
        },
        "timezone": "America/Chicago",
        "timezone_offset": -6,
        "timezone_offset_with_dst": -6,
        "date": "2024-03-02",
        "date_time": "2024-03-02 08:08:51",
        "date_time_txt": "Saturday, March 02, 2024 08:08:51",
        "date_time_wti": "Sat, 02 Mar 2024 08:08:51 -0600",
        "date_time_ymd": "2024-03-02T08:08:51-0600",
        "date_time_unix": 1709388531.471,
        "time_24": "08:08:51",
        "time_12": "08:08:51 AM",
        "week": 9,
        "month": 3,
        "year": 2024,
        "year_abbr": "24",
        "is_dst": false,
        "dst_savings": 0
    }
    '''
        s.close()
        data = [] # Will be a list of [year, month, date, hour, minute, second]
        #This part parses the JSON text to get year, month, date, hour, minute, and second
        startTime = timeJSON.json()['date_time'] #2024-01-04 15:05:54 for example
    #    print('Start time: ' , startTime)

        for x in startTime.split(' ')[0].split('-'): #Get the date parts
            data.append(int(x))

        for x in startTime.split(' ')[1].split(':'): #Get the time parts
            data.append(int(x))
        
        return data

    def time_from_internet(self, host, api_key):
        # Default return value to my birthday.  This can act as a flag that the rtc has not been retrieved from the internet.
        return_value = [2000,2,3,8,35,0,0] #RP2 doesn't like 1959 (I guess I'm really that old!)

        if not self.is_connected():
            print("No internet connection")
            raise InternetNotConnected
        else:
            #while max_retries > retry_count and not self.is_connected():
            self._setDateTimeURL(host, api_key) #Sets up a variable inside the class
            
            #Get the current time from the internet
            return_value = self.getDateTime()
                
        return return_value
            
