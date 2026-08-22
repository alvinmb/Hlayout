"""Small font helpers shared across the GUI.

Bold section-header labels (e.g. "RULES"/"VIOLATIONS" in the DRC
window, "SOLUTIONS"/"RESISTOR LIST"/"DESIGN SUMMARY" in the Thick-Film
Resistor Designer, the Layers table's own header row) want to be bold
but otherwise the same size as everything else around them. Hardcoding
a point size for them (as several of these used to) drifts out of sync
the moment App.UI_FONT_SIZE changes -- exactly the inconsistency
reported when most of the app was already a consistent 14pt but these
particular labels were still stuck at an old hardcoded 10pt. Deriving
the size from TkDefaultFont's *current* size instead means these
labels automatically track whatever the app's font size actually is.
"""

from tkinter import font as tkfont


def section_header_font(widget):
    """Bold variant of TkDefaultFont at its current size, scoped to
    the given widget's Tk interpreter (matters when a dialog is its
    own Toplevel/root)."""
    default_size = tkfont.nametofont("TkDefaultFont", widget).cget("size")
    return ("TkDefaultFont", default_size, "bold")
