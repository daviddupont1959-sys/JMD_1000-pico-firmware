"""
state.py - Shared mutable state for the JMD_1000 firmware.

Other modules `import state` and read/write its attributes directly
(e.g. `state.client`, `state.log_file = LogManager(...)`). Module-attribute
assignment is visible across every file that imports this module - this is
what replaces the old single-file pattern of `global <name>` declarations,
which only ever worked because everything used to live in one file.

Keep this file free of hardware/class construction logic; it should only
ever hold plain values and dicts so it stays small, side-effect free, and
safe for every other module to import without risking circular imports.
"""

# ----------------------------------------------------
# Firmware / notification constants
# ----------------------------------------------------
FW_REV = "3.00"
NTFY_URL = "https://ntfy.sh/jmd_1000_safety_alert_4784"

# ----------------------------------------------------
# MQTT connection settings
# ----------------------------------------------------
mqtt_server = 'broker.emqx.io'
#client_id = 'devices/JMD_1000'
client_id = 'JMD_1000'

# ----------------------------------------------------
# MQTT Topics
# ----------------------------------------------------
topics = {
    # ----------------------------------------------------
    # STATE + ALERT OUTPUT
    # ----------------------------------------------------
    "topic_motion"         : f"{client_id}/state/motion".encode(),
    "topic_last_motion"    : f"{client_id}/state/last_motion".encode(),
    "topic_alert"          : f"{client_id}/alert/inactivity".encode(),
    "topic_test_alert"     : f"{client_id}/alert/test".encode(),
    "topic_time"           : f"{client_id}/state/time".encode(),
    "topic_awake"          : f"{client_id}/state/awake".encode(),
    "topic_version"        : f"{client_id}/state/version".encode(),

    # ----------------------------------------------------
    # CONFIG PUBLISHING
    # ----------------------------------------------------
    "topic_cfg_full"       : f"{client_id}/config/full".encode(),

    # ----------------------------------------------------
    # COMMANDS (phones → device)
    # ----------------------------------------------------
    "topic_set_config"       : f"{client_id}/command/set_config".encode(),
    "topic_test_cmd"         : f"{client_id}/command/test_alert".encode(),
    "topic_reboot"           : f"{client_id}/command/reboot".encode(),
    "topic_sync_time"        : f"{client_id}/command/sync_time".encode(),
    "topic_cfg_get"          : f"{client_id}/command/get_config".encode(),
    "topic_update"          : f"{client_id}/command/update".encode(),
    "topic_get_version"     : f"{client_id}/command/get_version".encode(),
    "topic_get_log_list"    : f"{client_id}/command/get_log_list".encode(),
    "topic_get_log_file"    : f"{client_id}/command/get_log_file".encode(),

    # ----------------------------------------------------
    # RESPONSES (device → phones)
    # ----------------------------------------------------
    "topic_ack"            : f"{client_id}/response/ack".encode(),
    "topic_err"            : f"{client_id}/response/error".encode(),
    "topic_log"            : f"{client_id}/response/log".encode(),
    "topic_log_list"       : f"{client_id}/response/log_list".encode(),
    "topic_log_file"       : f"{client_id}/response/log_file".encode(),

    # ----------------------------------------------------
    # WILDCARD SUBSCRIPTIONS
    # ----------------------------------------------------
    "topic_cmd_all"        : f"{client_id}/command/#".encode(),
}

# Populated by mqtt_handlers.py at import time: {topic bytes -> handler fn}.
# Kept here (rather than exported from mqtt_handlers directly) so
# mqtt_client.mqtt_callback can look it up without importing mqtt_handlers -
# mqtt_handlers already depends indirectly on mqtt_client (via rtc_helpers),
# so importing it back from mqtt_client would create a circular import.
command_handlers = {}

# ----------------------------------------------------
# Runtime objects - assigned during boot in Detect.py, then read/written
# by the other modules as the firmware runs.
# ----------------------------------------------------
client = None
internet_manager = None
ble_manager = None
log_file = None
myConfig = None
rtc_manager = None
secrets = None
leds = {}

# WiFi credentials dict, loaded at boot and updatable over BLE ("PUT").
WiFi_Creds = None
