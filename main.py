import os
import sys
import time
import json
import machine

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

    try:
        import OneTimeCode   # run the script
    except Exception as e:
        # A broken one-time script must not stop the detector from starting.
        print("OneTimeCode failed:", e)

    try:
        os.remove("OneTimeCode.py")  # delete it so it runs only once
    except OSError:
        pass

# ----------------------------------------------------------------------
# Crash recovery (3.10)
# Detect.py normally never returns. If it raises, or returns, the Pico used to
# sit idle at the REPL until somebody power-cycled it. Now: record what
# happened, wait, and reset. Detect.py reports the record (log + push
# notification) on the next boot and clears the counter after 10 stable minutes.
#
# The wait doubles with each consecutive crash (10 s, 20 s, 40 s ... 10 min) so
# a deterministic bug cannot hammer the flash or the network in a tight loop.
#
# Development: create an empty file named "debug.flag" on the Pico to disable
# the automatic reset (the traceback prints and you stay at the REPL).
# Ctrl-C (KeyboardInterrupt) is deliberately not caught, so it still works.
# ----------------------------------------------------------------------
CRASH_FILE = "crash.json"
MAX_BACKOFF_S = 600


def _traceback_text(e):
    try:
        import io
        buf = io.StringIO()
        sys.print_exception(e, buf)      # MicroPython
        return buf.getvalue()
    except Exception:
        try:
            import traceback             # CPython (tests)
            return "".join(traceback.format_exception(type(e), e, e.__traceback__))
        except Exception:
            return "%s: %s" % (type(e).__name__, e)


def _read_crash():
    try:
        with open(CRASH_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _handle_crash(reason, e=None):
    tb = _traceback_text(e) if e is not None else reason
    print("Boot: Detect.py stopped:", reason)
    print(tb)

    rec = _read_crash()
    n = int(rec.get("n", 0)) + 1
    try:
        with open(CRASH_FILE, "w") as f:
            f.write(json.dumps({"n": n, "msg": tb.strip()[-220:]}))
        try:
            os.sync()
        except Exception:
            pass
    except Exception as werr:
        print("Could not write crash record:", werr)

    if "debug.flag" in os.listdir():
        print("debug.flag present: NOT resetting.")
        return

    delay = 10
    for _ in range(n - 1):
        delay = min(delay * 2, MAX_BACKOFF_S)
    print("Boot: crash #%d, resetting in %d s" % (n, delay))
    time.sleep(delay)
    machine.reset()


print("Boot: Starting Detect.py")

try:
    import Detect
except Exception as e:
    _handle_crash("%s: %s" % (type(e).__name__, e), e)
else:
    _handle_crash("Detect.py returned unexpectedly")
