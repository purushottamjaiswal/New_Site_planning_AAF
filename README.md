# New Site Planning AAF — standalone desktop application

Version 5.1. A local Python/Tkinter application for evaluating telecom site locations from Red/Amber coverage and speed grids, population or MR demand, existing Airtel sites, competitor context and building footprints. It runs outside QGIS.

This repository contains application source, synthetic tests and input templates. Operational telecom datasets, generated planning reports and building caches are not included. Select your own files in the UI; the initial data paths are placeholders for local inputs.

## Run on Windows

1. Clone or download this repository and extract it into a writable folder.
2. Install ordinary Python with Tkinter (Python 3.12 or 3.13 is a practical choice).
3. Double-click **Start_Planner.bat**. It creates a local `.venv` and installs `requirements.txt`.
4. Choose your input files under **Data sources** and **Buildings / maps**. Inspect their layers/CRS and confirm field mappings.
5. Select country, grid resolution, snapshots and planning rules, then click **Run planning**.

Alternatively, from an Anaconda Prompt or terminal in the repository folder:

```shell
python -m pip install -r requirements.txt
python app.py
```

Use your IT-approved package mirror if corporate connectivity restricts package downloads.

## Resizable desktop UI

The opening window fits the screen. Run/Cancel/scenario controls stay in a fixed footer; long forms scroll vertically and horizontally. The top section dropdown reaches tabs on narrow windows. **Ctrl+Enter** starts planning when no task is running. Existing scenario JSON files can be loaded with **Load scenario**.

## Data inputs

| Format | Notes |
| --- | --- |
| CSV / CSV.GZ | Attribute tables with mapped coordinates; building footprints can use polygon WKT |
| SHP | Keep `.shp`, `.dbf`, `.shx`, `.prj` and companions together |
| GPKG | Inspect and choose the required internal layer |
| KML / KMZ | Local vector placemarks and attributes; NetworkLinks/imagery are not downloaded |
| GeoJSON / Esri JSON | Polygon building arrays stream in disk mode; arbitrary JSON structures are not inferred |
| FileGDB `.gdb` | Select the complete folder, then its spatial feature layer |
| `.gdbtable` | Readable feature tables require their original companion files; prefer the complete `.gdb` |
| ZIP | Complete shapefile datasets or one complete `.gdb` folder |
| Other vector formats | Availability depends on the installed GDAL/pyogrio drivers |

Declared spatial CRSs are converted to WGS84. Supply the actual source CRS when missing or incorrect. Common field aliases are proposed; confirm their business meaning. Import support does not create missing coverage, population or survey attributes.

## Large building datasets

Select footprints under **Buildings**, then keep **Building storage → Disk index** selected. SQLite/RTree indices are built incrementally in `building_cache` and reused when the source fingerprint and import settings match. CSV WKT, GeoJSON and Esri polygon JSON stream; GDAL formats are read in chunks.

Disk mode has no fixed one-million-source-record, 200,000-footprint or 200 km dataset cap. Screening uses local metric batches of up to 256 candidates and 1,000 footprints. General planning tables retain their one-million-record guard and candidate generation retains its configured limit. Regional memory mode remains limited to 200,000 footprints and a 200 km span. Convert huge KML/KMZ to GPKG for scalable import.

Allow disk space for the source, index and outputs. Invalid geometry/confidence is audited. A footprint supplies mapped building evidence; it does not establish roof strength, ownership, access, population or buildability.

## Location planning

- **Area search** evaluates alternatives within the grid scope instead of requiring centroid placement.
- **Supplied candidates** preserves input tower, parcel or rooftop coordinates and their provenance.
- Real polygon grids constrain coordinates to the actual parent boundary and supply geodesic area. Optional no-build polygons and circular buffers reject excluded locations.
- Buildings support ground clearance, context-only scoring or unverified rooftop interior candidates.
- Optional finer demand points replace coarse grid demand with reconciled point totals. Building counts never become population automatically.
- Greedy marginal-demand selection enforces configured site spacing and reference-point limits. It is a planning heuristic, not an RF propagation or surveyed feasibility model.

Use Population mode only with real population data. **MR preview** is a proxy demonstration; MR records are not population, unique users, traffic or revenue.

## Maps and exports

**Results → OpenLayers / Google maps** provides Google Road/Satellite XYZ backgrounds and OpenStreetMap with site, grid, exclusion and building overlays. Online assets/tiles require connectivity and provider access. The alternate `planning_web_map.html` uses the official Google Maps JavaScript API with a user-supplied API key.

The local map server binds to `127.0.0.1:8765`. Keep the desktop app running for viewport footprint queries. Responses display at most 5,000 buildings and explicitly flag truncation; zoom in for detail. This display cap does not limit planning.

Each run creates a separate output folder with proposed-site CSV/GeoJSON, candidate assessments, demand assignments, settings/provenance, spatial constraints, full streaming footprint GeoJSON, an offline map and Google Earth KML/KMZ. Embedded and Earth building previews are capped at 20,000 footprints. Earth imagery/terrain are reviewed externally, not ingested automatically.

## Command-line use

Edit `examples/scenario_NG_buildings.json` to point to your own datasets, selected building layer and rules, then run from the repository root:

```shell
python planner.py --config examples/scenario_NG_buildings.json
```

Blank optional paths disable those sources. Paths are relative to the working directory. See `data/siting_templates` for candidate, detailed-demand, infrastructure and exclusion-buffer headers.

## Validation

```shell
python -m unittest discover -s tests -v
```

The synthetic suite covers input formats, reprojection, polygon topology, demand reconciliation, siting constraints, disk indexing, cancellation and map endpoints. The native geometry smoke check (`python tests/ui_resize_smoke.py`) requires a display; under Linux it can use Xvfb. See `VALIDATION.md` for measured checks and limits.
