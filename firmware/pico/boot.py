# CircuitPython boot.py: expose a second USB serial port for the gun protocol.
#
# The console port stays available for the REPL and print() debugging; the PC
# software talks JSON on the data port only. Takes effect after a hard reset.

import usb_cdc

usb_cdc.enable(console=True, data=True)
