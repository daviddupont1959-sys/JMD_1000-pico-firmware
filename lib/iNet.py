import network
import socket
import time
import urequests
import ujson
import machine
import sys


class InternetManager:
    
    def __init__(self, ssid, password, led_pin):
        self.ssid = ssid
        self.password = password
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self.connect_flag = False
        self.led = led_pin
        self.led.off() #Make sure it starts in the off state
        #Placehoders
        self._dateTimeURL = ""
        #The Wide Area Network address (WAN-IP) can be used to determine time zone.
        self._wan_ip = "172.56.93.182" #Will default to be in USA Central Time Zone
        # The next three lines will get the router IP from the internet
        try:
            self.connect(seconds = 10)
        except SystemExit as e:
            sys.exit(1)
    
        self.getRouterIP()
        self.disconnect()
        #print("INIT to: ", self.ssid, self.password)

    def connect(self, seconds = 5):
        self.wlan.active(True)
        if not self.connect_flag:
            print(f"Connecting to {self.ssid}...")
            self.wlan.connect(self.ssid, self.password)
            while not self.wlan.isconnected() and seconds > 0:
                print(f"Waiting {seconds} seconds for connection...")
                seconds -= 1
                time.sleep(1)
        if seconds == 0:
            self.led.off()
            self.connect_flag = False
            sys.exit(1) # ValueError("Unable to connect to internet.")
        else:
            self.led.on()
            self.connect_flag = True
            print("Connected, network config:", self.wlan.ifconfig())

    def disconnect(self):
        if self.connect_flag:
            self.wlan.disconnect()
            self.wlan.active(False)
            self.led.off()
            self.connect_flag = False
            print("Disconnected from WIFI.")

    def is_connected(self):
        return self.connect_flag

    def setDateTimeURL(self, host, api_key):
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
        #requires that setDateTimeURL ba called previously
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
        try:
            print("Connecting in time_from_internet.")
            self.connect(seconds = 5)
        except:
            print("No internet connection")
            # Default return value to my birthday.  This can act as a flag that the rtc has not been retrieved from the internet.
            return_value = [2000,2,3,8,35,0,0] #RP2 doesn't like 1959 (I guess I'm really that old!)
        else:
            #Getting here implies a good connection to the Internet.
            time.sleep(1) # Try a little delay to see if it helps.
            self.setDateTimeURL(host, api_key) #Sets up a variable inside the class
            
            #Get the current time from the internet
            return_value = self.getDateTime()

        finally:
            # To disconnect
            self.disconnect()
        return return_value
            
