import network
import time
from machine import Pin

class InternetHandler:
    def __init__(self, ssid, password, led_pin=None, timeout=10, max_retries=3, blink_interval=0.5):
        """
        ssid: Wi-Fi SSID
        password: Wi-Fi password
        led_pin: GPIO pin for connection LED (optional)
        timeout: seconds to wait per attempt
        max_retries: number of attempts before raising exception
        blink_interval: LED blink interval while trying
        """
        self.ssid = ssid
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries
        self.blink_interval = blink_interval
        self.led = Pin(led_pin, Pin.OUT) if led_pin is not None else None

        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)

        self.connect()

    def _blink_led(self, duration):
        """Blink LED while waiting."""
        if not self.led:
            time.sleep(duration)
            return
        end = time.ticks_add(time.ticks_ms(), int(duration * 1000))
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self.led.value(1)
            time.sleep(self.blink_interval)
            self.led.value(0)
            time.sleep(self.blink_interval)

    def connect(self):
        """Attempt to connect to Wi-Fi with retries, timeout, and LED blink."""
        for attempt in range(1, self.max_retries + 1):
            print(f"Connecting to Wi-Fi (attempt {attempt})...")
            self.wlan.connect(self.ssid, self.password)

            start = time.ticks_ms()
            while not self.wlan.isconnected():
                if time.ticks_diff(time.ticks_ms(), start) > self.timeout * 1000:
                    print("Connection attempt timed out")
                    break
                self._blink_led(self.blink_interval*2)  # blink in small steps

            if self.wlan.isconnected():
                print("Connected! IP:", self.wlan.ifconfig()[0])
                if self.led:
                    self.led.value(1)  # solid on
                return
            else:
                print("Retrying...")
                time.sleep(1)

        # Failed all attempts
        if self.led:
            self.led.value(0)
        raise RuntimeError("Failed to connect to Wi-Fi")

    def is_connected(self):
        return self.wlan.isconnected()

    def disconnect(self):
        self.wlan.disconnect()
        if self.led:
            self.led.value(0)
        print("Disconnected from Wi-Fi")

    def get_ip(self):
        if self.is_connected():
            return self.wlan.ifconfig()[0]
        return None
