"""
hwconfig.py - Which unit is this board, and how is it wired? (new in 3.12)

One HW_Config.json can describe every board, so the same file can sit on all
units and never needs copying. The board is recognised by the factory-burned
unique ID of its Pico (machine.unique_id()), which cannot be put on the wrong
board by mistake the way a copied file can.

A "unit" entry is:
    {"client_id": "JMD_1000", "motion_detector_pin": 28,
     "leds": {"Working": 20, "WIFI": 19, "BlueTooth": 18, "Motion": 17, "ALERT": 16}}

Accepted file formats
  Multi-unit (recommended):
      {"default": <unit>, "boards": {"<board id, lower-case hex>": <unit>, ...}}
      A board listed under "boards" gets its own entry. Any other board gets
      "default" and a warning (log + start-up notification) so a board that
      has not been listed yet is noticed, not silently mis-wired.
  Single-unit (3.02 to 3.11):
      the unit entry on its own. Still works unchanged.

If the file is missing or unusable, the built-in settings below are used (the
real unit, JMD_1000 on the redesigned hardware) and a warning is returned.
Imports nothing from state.py, so state.py can import this safely.
"""
import binascii
import machine
try:
    import ujson as json
except ImportError:
    import json

LED_NAMES = ("Working", "WIFI", "BlueTooth", "Motion", "ALERT")


def _builtin():
    # The real (deployed) unit: JMD_1000 on the redesigned hardware.
    return {"client_id": "JMD_1000", "motion_detector_pin": 1,
            "leds": {"Working": 9, "WIFI": 8, "BlueTooth": 7, "Motion": 6, "ALERT": 5}}


def board_id():
    """This Pico's unique ID as lower-case hex, e.g. 'e66164084348452b'."""
    try:
        return binascii.hexlify(machine.unique_id()).decode().lower()
    except Exception:
        return ""


def load():
    """Returns (unit_dict, source_text, warning_or_None). Never raises."""
    bid = board_id()
    try:
        with open("HW_Config.json", "r") as f:
            raw = json.load(f)
    except Exception as e:
        return _builtin(), "built-in defaults", "HW_Config.json unreadable (%s); using built-in default pins" % e

    warn = None
    try:
        if "boards" in raw or "default" in raw:
            boards = {}
            for k, v in raw.get("boards", {}).items():
                boards[str(k).lower()] = v
            if bid in boards:
                unit = boards[bid]
                src = "board %s" % bid
            else:
                unit = raw.get("default") or _builtin()
                src = "default entry"
                warn = "Board %s is not listed in HW_Config.json; using the default settings (unit %s)." % (
                    bid or "?", unit.get("client_id", "JMD_1000"))
        else:
            unit = raw
            src = "single-unit file"
        unit = dict(unit)
        if not unit.get("client_id"):
            unit["client_id"] = "JMD_1000"
        unit["client_id"] = str(unit["client_id"])
        int(unit["motion_detector_pin"])
        for name in LED_NAMES:
            int(unit["leds"][name])
        return unit, src, warn
    except Exception as e:
        return _builtin(), "built-in defaults", "HW_Config.json unusable (%s); using built-in default pins" % e
