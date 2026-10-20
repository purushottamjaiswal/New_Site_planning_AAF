# QGIS New Site Planning v2.0 / production core v7.0

Replace older plugin versions. The production worker uses the same canonical engine as the standalone app, with required land/water masks, no 10,000-site ceiling, policy choices, persistent result projects and draft review. The worker verifies its core manifest. Raw layer snapshots are temporary; derived outputs persist when enabled. Web maps run in the external planning Python, keeping scientific imports outside QGIS.

Read V7_PLANNING_GUIDE.md. Run Setup_Planning_Environment.bat outside QGIS to update dependencies, then select that external Python. Native GUI validation is pending; backend tests do not establish GUI/provider behaviour.

# New Site Planning — QGIS plugin 1.0.0

Private installable plugin targeting **QGIS 3.34–3.x**. Native QGIS dock, QGIS input layers and styled map outputs. The planning engine runs in an external Python process so it does not change QGIS's Python packages.

This is the QGIS counterpart of the desktop planning application. It includes your three CSV sources, the blank population template, optional-input templates and the real 370-footprint Lagos OSM sample. Population has not been fabricated.

**Validation status:** the engine, worker protocol and polygon rules pass headless tests. A QGIS installation is unavailable in the build environment, so live dock creation, provider geometry snapshots and rendering still need local validation. The plugin is marked experimental for that reason; this is not a published QGIS repository release.

## 1. Install the plugin ZIP

1. Download `NewSite_Planning_QGIS_Plugin.zip`. For plugin installation, **do not extract or rename the folder inside it**.
2. Open QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose the ZIP and click **Install Plugin**. Enable **New Site Planning** under Installed if it is not already enabled.
4. Open **Plugins → New Site Planning → New Site Planning**, or click its map-pin toolbar button.

The ZIP must contain exactly one plugin folder, `newsite_planning_qgis`, with `metadata.txt` and `__init__.py` immediately inside it. QGIS 4 is not declared compatible.

## 2. Set up the planning Python once

Use a separate Python 3.10+ environment; 3.12 or 3.13 is recommended. Do **not** run pip installation commands in the QGIS Python Console, and do not select `qgis-bin.exe` as the planning executable.

Two options:

- Reuse the desktop application's `.venv\Scripts\python.exe` if its Version 3 requirements are installed.
- Create the dedicated environment using the included helper.

For the helper on Windows:

1. Extract a second copy of the ZIP to a temporary folder, open `newsite_planning_qgis`, then double-click **Setup_Planning_Environment.bat**. This extracted copy is only for running the helper; the installed plugin remains managed by QGIS.
2. It creates a dedicated environment at `%LOCALAPPDATA%\NewSitePlanningQGIS\venv`, installs `requirements.txt`, checks versions and prints the executable path.
3. In the plugin's **Environment** tab, choose:

   ```text
   C:\Users\YOUR_USER\AppData\Local\NewSitePlanningQGIS\venv\Scripts\python.exe
   ```

4. Click **Check planning dependencies**. Confirm the success status and versions in Results/log.

Alternatively, run the helper from the installed plugin folder. Find that folder from **Settings → User Profiles → Open Active Profile Folder → python → plugins → newsite_planning_qgis**. The plugin's Environment setup instructions show its exact path.

For Linux/macOS, run `python3 setup_environment.py` with a standard Python outside QGIS. The helper prints the environment's `bin/python`. Your IT-approved package mirror may be used where corporate proxies block package downloads. Keep certificate verification enabled.

The worker needs numpy, pandas, scipy, Shapely 2 and pyproj. The native plugin itself imports none of those libraries into QGIS. It removes QGIS Python/Qt/PROJ environment overrides from the external process. Missing packages or unsupported package versions result in a clear dependency error. Setup does not modify the QGIS installation.

## 3. First trial using the supplied CSVs

1. Open the plugin. On Sources, leave the three bundled file paths selected.
2. Scope/score: country **NG**, resolution **1km**, grid snapshot **Sep'26**, Airtel snapshot **Sep_2026**, **MR preview**, maximum sites **30**, minimum density **0**.
3. Siting: choose **Area search**, spacing **250 m**. Defaults are trial planning assumptions, not an approved RF design.
4. Under Environment, select the prepared Python and an output directory.
5. Click **Run planning**.
6. Inspect the new QGIS layer group: proposed sites, grid assessment, Airtel active/pipeline/legacy inventory, competitors, demand representation and optional buildings/no-build polygons.
7. Open the result folder for all CSVs, JSON settings, source hashes, GeoJSON, online/offline maps and Google Earth files.

MR preview counts are not population, subscribers or unique users. The supplied data has no population. For population planning, complete a population template with actual values, load it, select Population mode and confirm People per grid vs People per km².

## 4. Use QGIS layers, GPKG, shapefile or PostGIS

Load your sources in QGIS normally. For each source role, choose **Project layer** and select the appropriate layer. Non-spatial vector tables are also usable where coordinates are available as attributes.

- **Grid:** polygon or point layer with grid ID, OPCO, resolution, period and combined category; MR count for preview mode.
- **Airtel:** point layer with site ID, status, technology, country and period.
- **Competitor:** point layer with operator, site ID, technology, country and historical period.
- **Population:** table keyed by OPCO and grid ID, with population or density values.
- **Candidates:** actual feasible candidate points with exact parent grid IDs. Feasibility must explicitly be Yes/True/1.
- **Detailed demand:** finer points with parent grid IDs and demand values that reconcile with grid totals.
- **Buildings:** local building polygons/multipolygons.
- **No-build polygons:** actual constraints, with polygon holes respected.

Map your column names under **Field mapping → Inspect source columns**. Required roles are marked with an asterisk. Shapefile field-name truncation may require explicit remapping.

With **Use layer geometry for WGS84 coordinates** checked, geometry is transformed from the layer's declared CRS to EPSG:4326 in a local snapshot. Source layers are not reprojected or edited. Single point geometry is required for site/demand/infrastructure roles; split multipart points first. Grid polygon centroids are used as coarse demand coordinates, while candidate coordinates are searched independently and checked against the actual polygon.

For polygon grid inputs, the plugin also exports the real WGS84 boundaries and **WGS84 ellipsoidal polygon areas**. Population/density conversions then use those areas, including clipped cells, instead of the nominal 1/9 km² assumptions. Point/CSV grids without actual polygon area still use nominal area.

Candidate points must be covered by their actual parent grid polygon. A nominal square candidate outside a coast-clipped or irregular polygon is rejected. On/in a supplied no-build polygon is rejected; polygon holes remain available unless another constraint excludes them. Invalid constraint geometries stop planning. Fix them using QGIS geometry tools first.

**Use selected grid features only** restricts project-grid snapshots to the selection and requires a non-empty selection. It does not restrict other sources. IDs, OPCO, grid resolution and periods must still match. In File mode, this selection control has no effect.

QGIS source snapshots are made on the GUI thread; only ordinary files are read by the external worker. Snapshot export shows progress and supports cancellation. Project layers are never accessed from the worker. Provenance records layer names/IDs, CRS, provider type and snapshot hashes; database connection URIs/passwords are not recorded by the plugin.

## 5. Siting and planning rules

The engine evaluates Red/Amber demand, active/reserved Airtel distances, existing grid site flags, competitor proximity, optional infrastructure limits, no-build buffers/polygons, actual grid polygons and optional building evidence.

**Area search** evaluates multiple locations at the chosen spacing, plus the grid centre. It does not claim that the search spacing is positional accuracy. **Supplied candidates** ranks real input coordinates and never silently substitutes a centroid. Legacy centroid mode is available for comparison. Greedy marginal demand is scored with severity, competitor context, distance decay, candidate suitability and optional mapped-building context. Each demand point is assigned once; partial-grid assignments are reported without claiming the full grid population.

A radius is not a propagation model. New-site spacing, pipeline reservation and site-present exclusions are configurable. Nearby legacy/off-air sites remain visible for upgrade/reactivation review. A speed issue can require capacity/transport remediation rather than another site; inspect optimization_review.csv.

Actual grid boundaries constrain coordinates, but Area search still generates a nominal search lattice around the input centre. Complex or very narrow polygons can yield few viable samples; increase search density or supply surveyed candidate coordinates when required. For full candidate/point schema details, see `DESKTOP_ENGINE_GUIDE.md`.

## 6. Open building data

For the bundled local OSM example, browse to `data/buildings/OSM_Lagos_selected_site_0p5km.geojson` within the plugin or your extracted copy. It contains 370 real mapped footprints around one Lagos proposal, not national building coverage.

To fetch another local OSM area:

1. Pan/zoom the QGIS map to the area of interest.
2. Set the OSM area radius (0.1–5 km).
3. Click **Fetch OSM buildings at map centre**.
4. The fetched layer is added to QGIS and selected as the building file input. Rerun planning to apply it.

This uses one bounded Overpass request. Errors, timeouts and empty data are reported; no-data is not interpreted as vacant land. Geometry/metadata and ODbL attribution are retained.

Google Open Buildings polygon WKT CSV/CSV.GZ and regional WGS84 GeoJSON are also accepted. Use a regional/town subset and optional south,west,north,east clip bbox. Building inputs are limited to 200 km spans and 200,000 accepted footprints; GeoJSON inputs are limited to 256 MB. The reader streams WKT CSVs and filters against actual geometry. No country-scale download happens automatically.

Building rules:

- **Ground clearance:** reject points inside/too close to a mapped building footprint.
- **Context only:** mapped-building count/area affects the optional score factor without a footprint exclusion.
- **Rooftop search:** in Area search mode generate footprint interior points for review; in Supplied candidates mode retain exact input coordinates and test footprint containment/area.

Mapped density does not create population. Footprint area is not usable roof area, occupancy, height, structural strength or ownership. Incomplete mapping can bias density context. A rooftop point remains unverified until surveyed and engineered. Water/protected land/roads require their own constraints or verified candidates.

## 7. OSM, Google Maps and Google Earth

**Add OpenStreetMap basemap to QGIS** creates an XYZ layer at the bottom of the layer tree. It needs internet. Retain © OpenStreetMap contributors attribution in any map layout you export. No tile prefetch or bulk download is provided.

**Open OSM / official Google viewer** serves the completed run at `http://127.0.0.1:8765`. Close a desktop-app map server first if that port is busy. OSM needs no Google key. Google Maps road/satellite/terrain uses the official Maps JavaScript API; supply your browser API key with billing enabled and referrer restriction `http://127.0.0.1:8765/*`. The key is not saved in scenarios or files. Google imagery is a review layer, not an undocumented QGIS XYZ endpoint.

**Open Google Earth KMZ** opens the export using its associated application. If Earth Pro is unavailable or file association is missing, open Earth web and import `planning_google_earth.kmz` manually. Proposed sites, active/planned/legacy inventory, competitors and building outlines are included. Elevations/heights are not invented; features are clamped to ground. The plugin does not extract Earth imagery/elevation or run terrain propagation.

## 8. Outputs, scenarios and cancellation

Each completed plan has its own output folder. Native GIS exports include:

| Output | Meaning |
| --- | --- |
| proposed_sites.geojson | Selected points and coordinate/source/evidence details |
| grid_assessment_spatial.geojson | Actual polygon geometry joined to assessment, when polygon input was supplied |
| grid_assessment_points.geojson | Grid points when actual polygon input was unavailable |
| airtel_spatial.geojson | Active/reserved/legacy roles |
| competitors_spatial.geojson | Historical competitor inventory |
| demand_points_spatial.geojson | Fine/coarse demand and assignments |
| building_footprints_used.geojson | Loaded mapped footprints and provenance |
| candidate_location_assessment.csv | All valid-coordinate alternatives and rejection reasons |
| qgis_layer_provenance.json | Source layer identifiers/CRS/provider/snapshot context |
| run_settings.json | Thresholds, summary, file names and SHA-256 hashes |

Other engine outputs include grid/site assignments, data-quality issues, optimization review, maps and Earth exports. Source layers are not restyled or overwritten; results use a new group. Empty result layers are skipped with a log message, and empty CSV/GeoJSON outputs remain available.

Save/Load scenario records fields, rules, paths, external interpreter and project layer IDs. Project layer IDs refer to that project; reselect layers after moving to a different project/computer. Setup and planning settings persist in QGIS user settings.

Cancel requests a cooperative stop and forces termination after 15 seconds if still running. It does not load incomplete results. Interrupted runs can leave partial output/job files for diagnosis. Completed jobs and snapshots persist in the chosen output directory; remove old run folders manually when no longer needed.

## 9. Validation and first local acceptance check

Headless tests cover the engine, worker and polygon adapter. The current build cannot certify the live QGIS GUI because QGIS is not installed in the build environment. An optional smoke test is included for QGIS's Python Console:

```python
from newsite_planning_qgis.tests.qgis_smoke import run
run(iface)
```

It creates temporary synthetic projected geometry, verifies WGS84 coordinate/area snapshots and instantiates the dock. It does not add or edit project layers. This script is supplied for local acceptance; it has not been executed in QGIS here.

From a standard Python environment, the packaged automated tests can be run from the plugin's parent directory:

```text
python -m unittest discover -s newsite_planning_qgis/tests -v
python newsite_planning_qgis/worker.py --check
```

See `VALIDATION.md` for the checks actually completed.

## Troubleshooting

- **No plugin entry:** confirm the ZIP was installed, the plugin is enabled, the root folder is intact and QGIS is 3.34–3.x. Restart QGIS if it cached a prior plugin copy.
- **Missing numpy/pandas/scipy/Shapely/pyproj:** select the dedicated external environment; do not install them into QGIS Python.
- **QGIS Python module/DLL error:** the worker should use a normal external Python. Check that you did not select the QGIS executable or interpreter with incompatible packages.
- **Missing source role/column:** inspect and map the actual source fields. Keep ID fields as text so leading zeros are preserved before QGIS reads them.
- **Nothing selected:** review thresholds, scope, valid demand, actual polygon checks, feasibility, minimum distances and pipeline/grid site flags in the candidate assessment.
- **Building extent mismatch:** select a footprint dataset that overlaps the planned region, or clear the building input for an unfiltered trial.
- **Constraint failure:** repair invalid polygons/CRS before retrying; no-build/parent-boundary failures are not silently ignored.
- **Map server port 8765 busy:** close the previous desktop/plugin viewer server or restart that application.

Official references: QGIS 3.34 plugin structure, vector layers and geometry APIs at https://docs.qgis.org/3.34/en/docs/pyqgis_developer_cookbook/ ; OSM attribution at https://www.openstreetmap.org/copyright ; Google Open Buildings at https://sites.research.google/gr/open-buildings/ ; Google Maps setup at https://developers.google.com/maps/documentation/javascript/get-api-key .
