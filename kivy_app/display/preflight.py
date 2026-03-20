import os
import subprocess


def display_ready() -> bool:
    display = os.environ.get("DISPLAY", "").strip()
    wayland = os.environ.get("WAYLAND_DISPLAY", "").strip()
    if wayland:
        return True
    if not display:
        print("❌ No DISPLAY set. Kivy GUI cannot open.")
        print("ℹ️  Run from Raspberry Pi desktop terminal, or export DISPLAY=:0 and XAUTHORITY=~/.Xauthority")
        return False

    try:
        result = subprocess.run(
            ["xset", "q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        if result.returncode != 0:
            print(f"❌ Cannot access X server on DISPLAY={display}")
            print("ℹ️  Check XAUTHORITY and run from local GUI session (not headless SSH)")
            return False
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"❌ Display preflight failed: {exc}")
        return False

    return True
