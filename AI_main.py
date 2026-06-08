import time, machine, network, ntptime, ujson
import bluetooth
from bluetooth import BLE

# === LED SETUP ===
leds = {
    "WIFI": machine.Pin(8, machine.Pin.OUT),
    "BlueTooth": machine.Pin(7, machine.Pin.OUT),
    "Motion": machine.Pin(6, machine.Pin.OUT),
    "ALERT": machine.Pin(5, machine.Pin.OUT),
    "Working": machine.Pin(9, machine.Pin.OUT)
}

motion_sensor = machine.Pin(1, machine.Pin.IN)
FIRMWARE_VERSION = "1.0.0"
LEVELS = {"DEBUG": 10, "INFO": 20, "ALERT": 30, "ERROR": 40}

# === GLOBALS ===
config = {}
last_motion_time = time.time()
last_wifi_attempt = 0
ble = BLE()
ble.active(True)
conn_handle = None

def log(msg, level="INFO"):
    if LEVELS[level] >= LEVELS.get(config.get("logLevel", "INFO"), 20):
        print(f"[{level}] {msg}")

def read_config():
    global config
    with open("config.txt") as f:
        config = ujson.loads(f.read())
    log("Configuration loaded", "DEBUG")

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(config["wifi_ssid"], config["wifi_password"])
    log("Connecting to WiFi...", "DEBUG")
    for _ in range(20):
        if wlan.isconnected():
            leds["WIFI"].on()
            log("WiFi connected", "INFO")
            return True
        time.sleep(1)
    leds["WIFI"].off()
    log("WiFi failed", "ERROR")
    return False

def sync_time():
    while True:
        try:
            ntptime.settime()
            log("Time synchronized", "INFO")
            break
        except:
            log("Time sync failed. Retrying in 10 minutes.", "ERROR")
            time.sleep(600)

def check_time_refresh():
    t = time.localtime()
    return t[6] == 6 and t[3] == 2 and t[4] == 5  # Sunday, 2:05am

def send_email(subject, message):
    import usocket as socket, ussl
    to_list = config["email_addresses"] + config["phone_numbers"]
    for recipient in to_list:
        log(f"Sending email/SMS to {recipient}", "DEBUG")
        try:
            addr = socket.getaddrinfo("smtp.gmail.com", 465)[0][-1]
            s = socket.socket()
            s.connect(addr)
            s = ussl.wrap_socket(s)
            s.send(b"EHLO pico\r\n")
            s.send(b"AUTH LOGIN\r\n")
            s.send(b"your_base64_encoded_username\r\n")
            s.send(b"your_base64_encoded_password\r\n")
            s.send(f"MAIL FROM:<your_email@gmail.com>\r\n".encode())
            s.send(f"RCPT TO:<{recipient}>\r\n".encode())
            s.send(b"DATA\r\n")
            s.send(f"Subject: {subject}\r\n\r\n{message}\r\n.\r\n".encode())
            s.send(b"QUIT\r\n")
            s.close()
            log("Message sent", "INFO")
        except Exception as e:
            log(f"Email failed: {e}", "ERROR")

# === BLE HANDLER ===
class BLEHandler:
    def __init__(self, config):
        self.led = leds["BlueTooth"]
        self.conn = None
        self.config = config

        svc_uuid = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        char_uuid = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
        self.config_char = (char_uuid, bluetooth.FLAG_READ | bluetooth.FLAG_NOTIFY)
        self.service = (svc_uuid, (self.config_char,))
        ((self.handle_config,),) = ble.gatts_register_services((self.service,))
        ble.irq(self.bt_irq)
        self.advertise()
        log("BLE advertising started", "INFO")

    def advertise(self):
        name = b"PicoMotion"
        adv_data = b'\x02\x01\x06' + bytes([len(name) + 1]) + b'\x09' + name
        ble.gap_advertise(100_000, adv_data)

    def bt_irq(self, event, data):
        global conn_handle
        if event == bluetooth._IRQ_CENTRAL_CONNECT:
            conn_handle = data[0]
            self.led.on()
            log("BLE client connected", "INFO")
            self.send_config()
        elif event == bluetooth._IRQ_CENTRAL_DISCONNECT:
            conn_handle = None
            self.led.off()
            self.advertise()
            log("BLE disconnected", "DEBUG")

    def send_config(self):
        if conn_handle is not None:
            json_data = ujson.dumps(self.config)
            ble.gatts_write(self.handle_config, json_data)
            ble.gatts_notify(conn_handle, self.handle_config, json_data)
            log("Sent config via BLE", "INFO")

# === MAIN LOOP ===
def main_loop():
    global last_motion_time
    alert_time = config["alert_time"]
    interval_time = config["interval_time"]
    ble_handler = BLEHandler(config)

    while True:
        now = time.time()

        # Check motion
        if motion_sensor.value():
            last_motion_time = now
            leds["Motion"].on()
            log("Motion detected", "DEBUG")
        else:
            leds["Motion"].off()

        # Check alert time
        if now - last_motion_time > alert_time:
            leds["ALERT"].on()
            send_email("Inactivity Alert", f"No motion at {config['location']} for {alert_time} seconds.")
        else:
            leds["ALERT"].off()

        # Time refresh
        if check_time_refresh():
            sync_time()

        leds["Working"].toggle()
        time.sleep(1)

# === BOOTSTRAP ===
read_config()

# Retry WiFi connect if needed
while not connect_wifi():
    time.sleep(600)

sync_time()

send_email("Device Startup",
           f"Device started at {config['location']}\nFirmware Version: {FIRMWARE_VERSION}")

main_loop()
