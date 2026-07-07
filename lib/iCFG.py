import ujson as json
import os

class ConfigManager:
    def __init__(self, filename="config.txt"):
        self.filename = filename
        self.config = {
            "thresholds": {
                "motion_interval": 10,
                "inactivity_alert": 60
            },
            "location": "4700 Rolling Water Dr.",
            "log_level": "DEBUG",
            "awake_window": {
                "start": {"hour": 21, "minute": 0},
                "end": {"hour": 6, "minute": 0}
            }
        }

    def _sanitize(self):
        """Coerce config values to their correct types.
        Guards against string values being sent where ints are expected,
        which would cause TypeError comparisons in the main loop.
        """
        try:
            t = self.config["thresholds"]
            t["inactivity_alert"] = int(t["inactivity_alert"])
            t["motion_interval"]  = int(t["motion_interval"])
        except (KeyError, ValueError, TypeError):
            pass
        try:
            w = self.config["awake_window"]
            w["start"]["hour"]   = int(w["start"]["hour"])
            w["start"]["minute"] = int(w["start"]["minute"])
            w["end"]["hour"]     = int(w["end"]["hour"])
            w["end"]["minute"]   = int(w["end"]["minute"])
        except (KeyError, ValueError, TypeError):
            pass

    def load_config(self):
        if self.filename in os.listdir():
            with open(self.filename, "r") as f:
                self.config.update(json.load(f))
            self._sanitize()
        else:
            self.save_config()

    def save_config(self):
            """
            Converts the internal configuration dictionary into a JSON string
            and saves it cleanly to flash storage.
            """
            import os
            
            try:
                # 1. Convert the RAM dictionary to a raw string first
                # Note: We do not use indent=4 here as MicroPython's json library doesn't support it
                json_string = json.dumps(self.config)
                
                # 2. Open and write the data cleanly to the file
                with open(self.filename, 'w') as file:
                    file.write(json_string)
                    
                # 3. FORCE the flash controller to commit the write operation immediately
                # This prevents files from corrupting if a machine.reset() happens right after
                try:
                    os.sync()
                except:
                    pass
                    
                print(f"Configuration successfully saved cleanly to {self.filename}")
                
            except Exception as e:
                print(f"Failed to save configuration file: {e}")

    def get_config(self):
        return json.dumps(self.config)

    def update_from_request(self, request: str):
        if "=" not in request:
            raise ValueError("Invalid request format. Use <key.path> = <value>")

        path, value_str = [part.strip() for part in request.split("=", 1)]
        keys = path.split(".")

        target = self.config
        for key in keys[:-1]:
            if key not in target or not isinstance(target[key], dict):
                raise KeyError(f"Invalid path: {path}")
            target = target[key]

        final_key = keys[-1]
        if final_key not in target:
            raise KeyError(f"Unknown key: {final_key}")

        current_value = target[final_key]
        new_value = self._convert_value(value_str, type(current_value))
        target[final_key] = new_value
        self._sanitize()

    def _convert_value(self, value_str, target_type):
        try:
            parsed = json.loads(value_str)
            return parsed
        except json.JSONDecodeError:
            pass

        if target_type is bool:
            return value_str.lower() in ("true", "1", "yes", "on")
        elif target_type is int:
            return int(value_str)
        elif target_type is float:
            return float(value_str)
        elif target_type is str:
            return value_str
        else:
            raise TypeError(f"Unsupported type: {target_type}")
