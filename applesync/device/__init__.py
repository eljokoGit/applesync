"""Device-access layer.

`base` defines the abstract contract. Two implementations:
- `afc`: a real device through pymobiledevice3 (usbmuxd + lockdown + AFC)
- `simulator`: a simulated library with fault injection, for the tests
"""
