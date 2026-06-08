import os
import time

def recurse(path):
    """Yield (path, filename) for all files under 'path'."""
    for item in os.listdir(path):
        full = path + "/" + item if path else item
        # Check if directory
        try:
            if os.stat(full)[0] & 0x4000:  # dir bit
                yield from recurse(full)
            else:
                yield path, item
        except OSError:
            pass


def install_tmp_files():
    for path, filename in recurse(""):
        if filename.endswith(".tmp"):
            original = filename[:-4]  # strip '.tmp'

            src = (path + "/" + filename) if path else filename
            dst = (path + "/" + original) if path else original

            try:
                # If the original exists, remove it first
                try:
                    os.remove(dst)
                    print(f"Replaced: {dst}")
                except OSError:
                    print(f"Added new file: {dst}")

                os.rename(src, dst)
            except OSError as e:
                print(f"Failed to install {dst}: {e}")

print("Boot: Installing any .tmp update files...")
install_tmp_files()

# Small delay for flash write completion
time.sleep(0.1)

if "OneTimeCode.py" in os.listdir():
    print("Executing one-time code.")

    import OneTimeCode   # run the script

    os.remove("OneTimeCode.py")  # delete it so it runs only once

print("Boot: Starting Detect.py")

try:
    import Detect
except ImportError as e:
    print("Failed to import Detect.py:", e)
