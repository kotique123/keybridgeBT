"""Entry point: python -m keybridgebt_win"""
import sys

if "--list-ports" in sys.argv:
    # Diagnostic: print all COM ports and exit so the user can identify the BT port
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No COM ports found.")
    else:
        print(f"{'PORT':<8}  {'DESCRIPTION':<45}  {'HWID'}")
        print("-" * 100)
        for p in sorted(ports, key=lambda x: x.device):
            print(f"{p.device:<8}  {(p.description or ''):<45}  {p.hwid or ''}")
    sys.exit(0)

from .main import main
main()

