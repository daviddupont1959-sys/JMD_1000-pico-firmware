import network
import time
import socket

class WiFiManager:
    def __init__(self, ssid, password, check_host="8.8.8.8", check_port=53,
                 reconnect_interval=5, check_interval=60):
        """
        WiFiManager handles WiFi connections with auto-reconnect.
        
        :param ssid: WiFi SSID
        :param password: WiFi password
        :param check_host: Host to test internet connectivity (default: Google DNS)
        :param check_port: Port for connectivity check
        :param reconnect_interval: Seconds to wait before reconnect attempts
        :param check_interval: Seconds between internet checks
        """
        self.ssid = ssid
        self.password = password
        self.check_host = check_host
        self.check_port = check_port
        self.reconnect_interval = reconnect_interval
        self.check_interval = check_interval
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self.last_check = 0

    def connect(self, timeout=15):
        """Try to connect to WiFi within given timeout (seconds)."""
        if not self.wlan.isconnected():
            print(f"Connecting to WiFi: {self.ssid}...")
            self.wlan.connect(self.ssid, self.password)
            start = time.time()
            while not self.wlan.isconnected():
                if time.time() - start > timeout:
                    print("WiFi connection timeout.")
                    return False
                time.sleep(1)
        print("Connected, IP:", self.wlan.ifconfig()[0])
        return True

    def is_router_connected(self):
        """Check if Pico is associated with WiFi router (basic check)."""
        return self.wlan.isconnected()

    def has_internet(self):
        """Optional: check if we can reach the internet."""
        try:
            addr = socket.getaddrinfo(self.check_host, self.check_port)[0][-1]
            s = socket.socket()
            s.settimeout(1)
            s.connect(addr)
            s.close()
            return True
        except:
            return False

    def ensure_connection(self):
        """Ensure WiFi connection, reconnect if router link is down."""
        if not self.is_router_connected():
            print("WiFi disconnected. Attempting reconnect...")
            self.wlan.disconnect()
            time.sleep(1)
            while not self.connect():
                print(f"Retrying in {self.reconnect_interval}s...")
                time.sleep(self.reconnect_interval)

        # Only check internet every `check_interval` seconds
        now = time.time()
        if now - self.last_check > self.check_interval:
            self.last_check = now
            if not self.has_internet():
                print("Warning: Router connected but no internet.")

    def get_ip(self):
        """Return current IP address or None."""
        if self.wlan.isconnected():
            return self.wlan.ifconfig()[0]
        return None
