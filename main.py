import os
import time

def install_tmp_files():
    for filename in os.listdir():
        if filename.endswith(".tmp"):
            original = filename[:-4]  # strip '.tmp'
            try:
                if original in os.listdir():
                    os.remove(original)
                    print(f"Replaced: {original}")
                else:
                    print(f"Added new file: {original}")
                os.rename(filename, original)
            except OSError as e:
                print(f"Failed to install {original}: {e}")

print("Boot: Installing any .tmp update files...")
install_tmp_files()

# Small delay for flash write completion
time.sleep(0.1)

print("Boot: Starting Detect.py")

try:
    import Detect
except ImportError as e:
    print("Failed to import Detect.py:", e)
