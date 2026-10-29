"""hlayout: Thick-Film Hybrid Layout CAD tool.

Core package. See spec.odt (project spec) for the original requirements:
  - Database units: 1 micron (0.001 mm)
  - Max database size: 500 x 500 mm
  - Default file format: GDSII
  - 64 layers, 9 colour/stipple patterns
  - Primitives: polygon (<=200 pts), path (<=200 pts, width, round/square caps),
    rectangle, text, sref (cell instance)
  - Python scripting to read/write layout data
"""

__version__ = "0.6.0"
