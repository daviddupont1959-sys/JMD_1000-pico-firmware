"""
mqtt_client.py - MQTT connection management: connect/reconnect, the
top-level message callback, a generic publish helper, and periodic
time/awake-state publishing.
"""
import time
from umqtt_simple import MQTTClient
import state

def termMsg(topic, message_text):
    #These are now going to the MQTT broker
    print(message_text)
    #Need to add code to send alerts to the MQTT broker
    if state.client != None:
        try:
            if isinstance(message_text, str):
                message_text = message_text.encode()

            state.client.publish(topic, message_text)
        except (SystemExit, OSError) as e:
            print(f"Trouble publishing: {message_text} to {topic}: {e}")
            state.log_file.error(f"Trouble publishing: {message_text} to {topic}: {e}")

def mqtt_callback(topic, msg):
    try:
        msg_str = msg.decode()
    except:
        print("MQTT decode error")
        return

    handler = state.command_handlers.get(topic)

    if handler:
        handler(msg_str)
    else:
        print("Unknown topic:", topic)
        state.client.publish(state.topics["topic_err"], b"ERR: unknown topic")

def connect_mqtt(retries=5, delay=2):
    client = MQTTClient(
        state.client_id,
        state.mqtt_server,
        user='PicoMan',
        password='Pico2Stuff',
        port=1883,
        ssl=False,
        keepalive=60
    )

    client.set_callback(mqtt_callback)

    for attempt in range(1, retries + 1):
        try:
            print(f"Connecting to MQTT broker (Attempt {attempt}/{retries})...")
            # Some micro-libraries return a status code (0 = success)
            res = client.connect(timeout=5)

            if res == 0 or res is None:
                print(f'Successfully connected to {state.mqtt_server} as {state.client_id}')
                client.subscribe(state.topics["topic_cmd_all"])
                print(f'Subscribed to {state.topics["topic_cmd_all"]}')
                return client
            else:
                print(f"Broker rejected connection with code: {res}")

        except OSError as e:
            # Catches network errors, connection timeouts, and unreachable hosts
            print(f"Network error during connection: {e}")
        except Exception as e:
            # Catches other unexpected errors without blocking KeyboardInterrupt
            print(f"Unexpected error: {e}")

        if attempt < retries:
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)

    # If we exhaust all retries, handle the failure explicitly
    print("Failed to connect to MQTT broker after multiple attempts.")
    return None

def publish_time():
    # Get the current Unix timestamp
    current_seconds = time.time()

    # Convert to a local time tuple
    # The tuple contains: (year, month, mday, hour, minute, second, weekday, yearday)
    time_tuple = time.localtime(current_seconds)

    # Extract individual components for easier formatting
    year, month, day, hour, minute, second, weekday, yearday = time_tuple

    # Example: Basic HH:MM:SS format
    formatted_time_basic = "{:02d}:{:02d}:{:02d}".format(hour, minute, second)

    termMsg(state.topics["topic_time"], f"publishing: {formatted_time_basic} to {state.topics['topic_time']}")
    # Get the True/False value for awake state and convert it to text
    awake_payload = str(state.rtc_manager.is_in_awake_window(state.myConfig.config["awake_window"]))

    try:
        state.client.publish(state.topics["topic_time"], formatted_time_basic.encode())
        state.client.publish(state.topics["topic_awake"], awake_payload)
    except OSError as e:
        print(f"publish_time failed ({e}), checking WiFi...")
        # Check WiFi first — MQTT reconnect is pointless if WiFi is down
        if not state.internet_manager.is_connected():
            print("WiFi lost — attempting reconnection...")
            state.leds["WIFI"].off()
            try:
                state.internet_manager.connect()
            except Exception as wifi_err:
                print(f"WiFi reconnection failed: {wifi_err}")
                return  # Give up this cycle, retry next time
        # WiFi is up (or just reconnected) — reconnect MQTT
        try:
            state.client.disconnect()
        except:
            pass
        state.client = connect_mqtt()
