"""Entry point: python -m keybridgebt_win"""
import sys

# --host <ip>  quick override (skips the interactive prompt)
host_override = None
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--host" and i < len(sys.argv):
        host_override = sys.argv[i + 1]
        break
    if arg.startswith("--host="):
        host_override = arg.split("=", 1)[1]
        break

from .main import main
main(host_override=host_override)

