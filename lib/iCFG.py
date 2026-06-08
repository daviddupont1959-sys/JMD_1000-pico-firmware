import ujson as json
import os

class ConfigManager:
    def __init__(self, filename="config.txt"):
        self.filename = filename
        self.config = {
            "wifi_ssid": "",
            "wifi_password": "",
            "interval_time": 10,
            "alert_time": 300,
            "email_addresses": [],
            "phone_numbers": [],
            "location": "",
            "logLevel:": ""
            }

    def load_config(self):
        if self.filename in os.listdir():
            with open(self.filename, "r") as f:
                self.config.update(json.load(f))
        else:
            self.save_config()  # Save default config if file doesn't exist

    def save_config(self):
        with open(self.filename, "w") as f:
            json.dump(self.config, f)

    def get_config(self):
        t = json.dumps(self.config)
        return t