"""
utils.py - Small stand-alone helpers with minimal dependencies.
"""
import time
import state

# Explicit top-down order, independent of dict iteration behavior
led_order = ["Working", "WIFI", "BlueTooth", "Motion", "ALERT"]

def lamp_test():
    for pin in state.leds.values():
        pin.off()
    for i in range(3):
        print("LED Test loop:", i + 1)
        for name in led_order:
            pin = state.leds[name]
            pin.on()
            time.sleep(.25)
            pin.off()

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
