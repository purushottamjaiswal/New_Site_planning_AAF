# New Site Planning AAF — standalone desktop application

Version 6.1. A local Python/Tkinter application for evaluating telecom site locations from Red/Amber coverage and speed grids, population or MR demand, existing Airtel sites, competitor context and building footprints. It runs outside QGIS.

This repository contains application source, synthetic tests and input templates. Operational telecom datasets, generated planning reports and building caches are not included. Select your own files in the UI; the initial data paths are placeholders for local inputs.

## Required water protection and grid update

New runs require **Land polygons** and **Water polygons** in Data sources. The tool rejects locations outside/on the coastal land boundary or inside/on water polygons. It stops when either source is missing or empty. Use current, sufficiently detailed coastal land and inland-water evidence covering the full planning area, including rivers and Lagos lagoon. Generic country outlines or ocean-only polygons do not establish inland-water coverage. Buildings and background imagery do not provide this evidence. Existing scenarios must add these paths and be rerun. See [WATER_AND_GRID_GUIDE.md](WATER_AND_GRID_GUIDE.md).

All grid records now have polygon footprints. Supplied grid polygons remain unchanged. CSV-only grids use nominal 1 km or 3 km squares reconstructed around their source centroids, with explicit geometry provenance. All viewers and the full grid GeoJSON use these footprints.

## Run on Windows

1. Clone or download this repository and extract it into a writable folder.
2. Install ordinary Python with Tkinter (Python 3.12 or 3.13 is a practical choice).
3. Double-click **Start_Planner.bat**. It creates a local `.venv` and installs `requirements.txt`.
4. Choose your inputs, including Land polygons and Water polygons, under **Data sources** and optional building footprints under **Buildings / maps**. Inspect their layers/CRS and confirm field mappings.
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

## Temporary databases and memory use

All normal desktop/CLI imports stream into a private SQLite session in the operating system's temporary directory. Source rows, mapped attributes, grid/polygon geometry, references, fine demand, candidate assessments, assignments and selected sites are stored on disk. Buildings use a temporary SQLite/RTree index inside that session.

The global lazy-greedy queue is also a database index. Candidate–demand neighbors are queried when needed instead of retaining a national neighbor graph, Python heap or full input DataFrames. SQLite cache size is configured to 16 MiB for the main session; that is one component of process memory, not a total-RAM guarantee. Temporary SQL work uses file-backed storage and database memory mapping is disabled.

**Buildings / maps → Building import batch rows** controls vector/JSON footprint read batches (default 400,000; range 100–400,000). This setting is independent of **Location search → Input batch rows**, which controls other source and working batches (default 2,000; range 100–20,000). Building CSV/WKT inputs continue streaming one record at a time. A 400,000-polygon batch can use substantial RAM for complex geometry; lower the building setting if needed. The complete accepted footprint index stays on disk. Building screening uses local batches of up to 256 candidates and 1,000 footprints. Source preview reads at most 300 records. The run log is bounded, and Results shows 200 sites per page with Previous/Next buttons.

Normal runs no longer apply the former one-million-table-row, 200,000-building, 200 km building-span or ten-million-link memory guards. The configurable **Maximum location alternatives** remains a deliberate work/disk budget; it can be set above one million. CSV/GZ, JSON and KML/KMZ have streaming readers; other supported vector layers use GDAL chunks. Some GDAL drivers or individual giant geometries may still require additional resources internally.

The session stays available while you review results and use live map queries. Starting a replacement run or closing the application removes working database copies; your original input files are never deleted. Cancellation and failed runs clean up their session, and command-line runs close it after export. A forced process termination or OS failure can leave temporary files behind. Completed output files remain in your selected output directory. Large sources therefore require sufficient disk space for input tables, indices and exports.

The `plan_frames` function remains a small-fixture in-memory reference for regression tests; the application and CLI always use database-backed `run_paths`.

## Planning more than 10,000 sites

Set **Planning rules → Maximum proposed sites** to a positive integer such as `20000` or `50000`. The former 10,000-site cap is removed. Set **Maximum location alternatives** high enough for the chosen area-search spacing and scope; finer spacing creates more candidate work.

A requested count is an upper target. The engine stops when feasible spaced candidates or incremental demand are exhausted, so it does not fabricate extra sites to reach the target. Selection remains global lazy-greedy over the selected country/resolution/snapshot, with exact spherical radius/spacing checks. Full site CSV/GeoJSON and Earth point exports stream every selected site, including those beyond the first UI page.

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

The local map server binds to `127.0.0.1:8765`. Both **Open interactive offline map** and **OpenLayers / Google maps** use the local server for viewport queries. The offline viewer needs no internet tiles or external scripts. Keep the desktop app running to query areas beyond its initial embedded preview. Viewport responses display at most 5,000 features per layer and explicitly flag truncation; zoom in for detail. This display cap does not limit planning. Sites, grids, inventory and footprints are queried from the active temporary databases.

Each run creates a separate output folder with proposed-site CSV/GeoJSON, candidate assessments, demand assignments, settings/provenance, spatial constraints, full streaming footprint GeoJSON, an offline map and Google Earth KML/KMZ. Static previews contain at most 500 planned sites, 5,000 grids and 5,000 records per inventory; building previews are capped at 5,000 footprints. Static previews state these limits. Earth streams every site/inventory point and up to 5,000 building footprints; full footprint geometry remains in GeoJSON. Earth imagery/terrain are reviewed externally, not ingested automatically.

## Command-line use

Edit `examples/scenario_NG_buildings.json` to point to your own datasets, selected building layer and rules, then run from the repository root:

```shell
python planner.py --config examples/scenario_NG_buildings.json
```

Land and water polygon paths are mandatory. Blank optional paths disable the other optional sources. Paths are relative to the working directory. See `data/siting_templates` for candidate, detailed-demand, infrastructure and exclusion-buffer headers.

## Validation

```shell
python -m unittest discover -s tests -v
```

The synthetic regression suite covers input formats, reprojection, polygon topology, demand reconciliation, siting constraints, database/reference-engine agreement, cleanup and map endpoints. A measured scale trial imported 1,010,051 population rows, selected/exported 10,050 sites and peaked at 148.71 MiB Linux process RSS for that fixture. These are measurements, not fixed resource guarantees. The native geometry smoke check (`python tests/ui_resize_smoke.py`) requires a display; under Linux it can use Xvfb. See `VALIDATION.md` for measured checks and limits.
