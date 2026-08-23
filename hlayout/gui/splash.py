"""Startup splash screen.

Shown once, on launch, for a few seconds while the app's branding is on
screen before the main window appears -- purely cosmetic, no work happens
during it.
"""
import os
import tkinter as tk

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_SPLASH_IMAGE = os.path.join(_ASSETS_DIR, "splash.png")

DEFAULT_DURATION_MS = 5000


def show_splash(duration_ms: int = DEFAULT_DURATION_MS, image_path: str = _SPLASH_IMAGE) -> None:
    """Show the HLayout splash screen for `duration_ms` milliseconds, then
    close it.

    Blocks until the splash closes (it runs its own short-lived Tk
    mainloop) -- call this *before* creating the main App window, since Tk
    only supports one active root at a time.

    Silently does nothing if the splash image can't be found or loaded, so
    a missing/corrupt asset never prevents the app from starting.
    """
    if not os.path.isfile(image_path):
        return

    splash = tk.Tk()
    splash.overrideredirect(True)  # no title bar / window borders

    try:
        image = tk.PhotoImage(file=image_path)
    except tk.TclError:
        splash.destroy()
        return

    width, height = image.width(), image.height()
    screen_w = splash.winfo_screenwidth()
    screen_h = splash.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")

    # Keep the splash above other windows while it's up.
    splash.attributes("-topmost", True)

    label = tk.Label(splash, image=image, borderwidth=0)
    label.image = image  # keep a reference alive -- Tk drops it otherwise
    label.pack()

    splash.after(duration_ms, splash.destroy)
    splash.mainloop()
