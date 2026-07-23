"""
notify.py - Push notifications via ntfy.sh.
"""
import urequests
import state

def send_inactivity_alert(message_text):
    """
    Sends a high-priority push notification directly to your phone 
    when the 8-hour inactivity threshold is crossed.
    """

    # 2. Set up the headers to format the notification on your phone
    headers = {
        "Title": "🚨 DAD'S HOUSE ALERT 🚨",
        "Priority": "high",       # 'high' or '5' ensures it stands out on your phone
        "Tags": "warning,house",   # Adds a warning emoji and house emoji automatically
    }

    # 3. Define the message payload text
#    message_text = f"No motion has been detected at Dad's house for {quiet_time} hours. Please check in."

    try:
        # Send the POST request to the ntfy cloud server
        response = urequests.post(state.NTFY_URL, data=message_text, headers=headers)

        # MicroPython Best Practice: Always check status and close socket connections!
        if response.status_code == 200:
            print("Push notification delivered to ntfy.sh successfully.")
        else:
            print(f"Server responded with an error code: {response.status_code}")

        response.close()

    except Exception as e:
        # Catches network timeouts, temporary Wi-Fi drops, etc.
        print(f"Failed to transmit push notification: {e}")
