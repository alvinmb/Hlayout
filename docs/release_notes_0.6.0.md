HLayout 0.6.0 brings the full thick-film hybrid flow, from a KiCad schematic to print artwork, bond diagrams, assembly drawings and 3D models, plus a built-in way to report bugs.

## New in 0.6.0
- **Help → Report Bug…** collects what's needed to fix a problem: versions, the design's state, every error this session with its traceback, recent commands, and any freeze dumps. Optionally it adds the open design and a screenshot. You can preview everything, then save it as one `.zip` or open a pre-filled GitHub issue. Nothing is uploaded automatically.
- When an unexpected error appears, HLayout now offers to report it, with the error already filled in.
- User manual: new chapter 24, *Reporting a Bug*.

## Highlights since 0.1.0
- **Auto Place** from a KiCad netlist: shortest weighted net length with power and ground included, a 50 µm grid, a thermal term that keeps hot parts apart, and straight-line interconnects that follow every drag.
- **Autorouting with Freerouting** for two-conductor multilayer designs and chip-and-wire hybrids, where wire-bond landings become bond fingers. Afterwards it removes vias, shortens tracks and removes jogs.
- **Chip-and-wire hybrid router** for single-conductor designs with wire-bond jumpers, plus a bond diagram in bonding order for the wire bonder.
- **Chip Wizard** for bare dies, wire bonds, Remove Unused Bonds, Bond Header Pins, and Kovar DIP and metal header packages.
- **Verification:** DRC with substrate-edge clearance and wire-bond length and angle rules, netlist extraction that includes wire bonds, and **thermal analysis** with banded temperature maps.
- **Documentation:** a one-step Assembly Drawing with the top view and the pin, resistor and bond tables, and a hidden-line Top View.
- **Manufacturing outputs:** Gerber and DXF export sets, pick-and-place (CSV or KiCad `.pos`) and test netlists.
- **3D:** FreeCAD export of the fired film stack, a STEP component library with auto-matching, and the SolidCraft 3D view.
- **Scripting:** the command bar, a Python Session API, and an MCP server so Claude can drive HLayout.

## Downloads
| System | File |
|---|---|
| Windows 10/11, 64-bit | `HLayout-0.6.0-windows-setup.exe` |
| Debian, Ubuntu and derivatives, x86-64 | `hlayout_0.6.0_amd64.deb` (`sudo apt install ./hlayout_0.6.0_amd64.deb`) |
| macOS, Apple silicon | `HLayout-0.6.0-macos.dmg` |

The installers are not code-signed. On Windows, if SmartScreen appears, choose **More info → Run anyway**. On macOS, the first time you open the app, Control-click HLayout and choose **Open**. Other systems can run from source: `pip install -r requirements.txt`, then `python hlayout_app.py`.

**Full changelog:** https://github.com/alvinmb/Hlayout/compare/v0.1.0...v0.6.0
