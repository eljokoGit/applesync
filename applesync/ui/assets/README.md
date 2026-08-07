# UI assets

Drop-in slot, read at start-up. Nothing here is required: if a file is
missing, the application falls back to the platform default.

| File       | Used for                                          | Format                                         |
|------------|---------------------------------------------------|------------------------------------------------|
| `icon.png` | window and taskbar icon at run time               | PNG, square, 1024x1024, transparent background |
| `icon.ico` | icon compiled into `AppleSync.exe` by PyInstaller | ICO, 16 to 256 px                              |

The `.ico` carries a distinct rendering per size: below 32 px the mark is
enlarged and the tile margin dropped, because at that scale the breathing
room costs more legibility than it buys.
