"""python -m sleepy_proton <sync|photos|gui|check> [options]"""
import os
import sys


def main():
    # pythonw.exe (Windows background runs) has no console: give print() somewhere to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    tools = ("sync", "photos", "gui", "check")
    if len(sys.argv) < 2 or sys.argv[1] not in tools:
        print(f"Usage: python -m sleepy_proton <{'|'.join(tools)}> [options]")
        return 2
    tool, args = sys.argv[1], sys.argv[2:]
    if tool == "sync":
        from .sync import main as run
    elif tool == "photos":
        from .photos import main as run
    elif tool == "check":
        from .check import main as run
    else:
        from .gui import main as run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
