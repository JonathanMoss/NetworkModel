"""FastAPI router package organizing API endpoints by domain concern.

Submodules:
  - ``tps``: Schematic TPS overview, viewport, search, pick, timing loads, and catalog.
  - ``nwr``: OSGB36 NWR Track Model overview, viewport, search, pick, asset inspector,
    and persistent track overrides.
  - ``network_fusion``: Network Fusion (GeoFurlong) status, corridor profile, and nearest point.
  - ``assets``: Detailed TPS asset inspector (``/api/asset/{asset_type}/{asset_id}``).
"""

