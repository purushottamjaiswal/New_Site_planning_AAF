# New Site Planning Tool — Version 3

A local Python desktop tool for selecting site search locations using Red/Amber grid demand, population or MR counts, Airtel sites and competitor context. The planner evaluates alternative coordinates instead of forcing every proposed site onto a grid centroid. It also supports exact supplied candidate coordinates, finer demand points and explicit siting constraints.

**A desktop optimum is not a surveyed tower location.** Your current three files contain no terrain, land ownership, detailed population, road geometry, exclusion polygons or actual grid boundaries. Generated locations are marked as desktop search points with physical feasibility unknown. For selection from actual tower, rooftop or land-parcel coordinates, use **Supplied candidates** mode. This preserves the input coordinates and reports their provenance without inventing measurement accuracy.


## Version 3: buildings, Google Maps and Google Earth

### Open building footprints in planning

A real **370-footprint OSM sample** is included in `data/buildings/OSM_Lagos_selected_site_0p5km.geojson`. It covers only the requested local area around the first Lagos proposal. It is not national building coverage. The metadata records its request bounding box, retrieval time, ODbL attribution and one unsupported OSM feature. Use `preview_NG_buildings.json` for the real-data demonstration.

To fetch buildings for another proposed site:

1. Run planning and select a site in Results.
2. Open **Buildings / maps → Use selected result site**.
3. Set a radius from 0.1 to 5 km and click **Fetch OSM footprints**. The download is a single bounded Overpass query, not a country-wide scrape.
4. Select a building rule and rerun planning. A downloaded dataset is not applied to a run already completed.

OSM building ways and polygon relations (including holes) are supported; incomplete/unsupported geometries are counted. No-data/timeouts stop the fetch with a message; empty data is not interpreted as empty land. Download geometry is deduplicated. OSM map tiles are for interactive viewing only; no tile caching/prefetch or bulk download is implemented.

For larger open datasets, use **Google Open Buildings downloads**, choose a regional polygon dataset, and load its WKT `geometry` CSV/CSV.GZ or a WGS84 GeoJSON polygon export. Point-only downloads cannot provide footprint clearance. Use **Building clip bbox** (`south,west,north,east`) to restrict a large WKT CSV while streaming; select a local dataset for GeoJSON. GeoJSON inputs are limited to 256 MB; WKT CSV inputs stream row by row and clip using the actual polygon rather than the CSV centroid hint. Imported footprints must span no more than 200 km and contain at most 200,000 accepted polygons. These limits keep a local metric projection and memory use practical.

Google Open Buildings includes inferred outlines and confidence scores; it is distinct from Google Maps/Google Earth imagery. The default confidence filter is 0.75 where a score exists. OSM features without model-confidence attributes are retained; this does not assign them a probability. Follow original source license/attribution requirements. Machine-detection confidence is not a coordinate-accuracy certification, and newer imagery may be misaligned with an older footprint dataset.

### Building rules

| Rule | Effect on site planning |
| --- | --- |
| Ground clearance | Reject candidate coordinates inside or within the configured metre clearance of a loaded footprint; default clearance 10 m |
| Context only | Add mapped building counts and footprint area within the context radius without a footprint siting exclusion |
| Rooftop search | Require a containing footprint meeting the minimum footprint-area proxy; in Area search mode, generate polygon interior points as roof-review candidates |

The footprint interior point is used because the geometric centre of an irregular polygon can lie outside it. Holes are respected. In Supplied candidates mode, rooftop screening preserves the exact candidate coordinates. Rooftop search is not allowed with the legacy centroid mode.

Optional mapped-density score factor:

`building factor = 1 + boost × min(log(1 + mapped building count) / log(101), 1)`

Defaults: context radius 500 m, maximum boost 0.15 and minimum roof footprint proxy 80 m². This factor multiplies the existing candidate score; it **does not create or redistribute population/MR demand**. A count of zero means no mapped evidence within that radius, not proof of no buildings. Regional data can have incomplete or uneven coverage. Outside the loaded evidence area, footprint feasibility remains unknown. Without an input footprint file, no building screening is performed and the factor is 1. Rooftop search requires a footprint file.

Footprints are transformed into a regional azimuthal-equidistant metric CRS before measuring buffers, distances and areas. The computed area is ground footprint area, not usable roof area, height, occupancy, floor count, strength, access, ownership or suitable antenna space. Open footprints cannot establish those attributes. Ground clearance does not exclude water, roads or protected land unless appropriate separate constraints/candidates are supplied.

Selected sites and the full alternative assessment include mapped-building count, nearby footprint area, nearest mapped building within the configured search radius, containing building ID/area, building score factor and evidence status. Unknown distances remain blank. `building_footprints_used.geojson` preserves source geometry, metadata and computed areas. Viewer drawing is limited to 20,000 footprints with a notice; the full loaded set remains in the exported GeoJSON and Earth files.

### OpenStreetMap and Google Maps layers

After planning, choose **Results → OSM / Google map layers**. The app serves the output on `http://127.0.0.1:8765` while it is open. Port 8765 must be free. This local viewer provides proposed, existing/reserved Airtel, competitor, grid-centre and building overlays.

- **OpenStreetMap** uses interactive OSM tiles and visible contributor attribution; it needs internet but no Google key.
- **Google Maps** uses the official Maps JavaScript API, with road/satellite/hybrid/terrain controls. Enable that API and billing in your Google Cloud project. Create a browser API key restricted to the Maps JavaScript API and the HTTP referrer `http://127.0.0.1:8765/*`. Enter it in the map page and click **Load Google map**. The key is not stored in application scenarios or output files.
- Selecting a proposed site centres both viewers, and provides a Google Maps search link that works without this app's embedded API key.
- The existing **offline map** remains available with local overlays, including mapped buildings, when internet access is unavailable.

Google Maps imagery is visual review context. The app does not call undocumented Google tile endpoints, download imagery or extract land cover/elevation/rooftop suitability from it. A missing/unconfigured Google key leaves OSM/offline views available. Google APIs/imagery can incur charges under your account; check your Cloud project settings.

To manually serve a completed output without the desktop UI, open a terminal in that run's output folder:

```text
python -m http.server 8765 --bind 127.0.0.1
```

Then open `http://127.0.0.1:8765/planning_web_map.html`. Stop the server with Ctrl+C.

### Google Earth review

Each run exports **planning_google_earth.kml** and **planning_google_earth.kmz** with proposed sites, Airtel inventory/planned roles, historical competitor sites and loaded building polygons. Coordinates are longitude, latitude, 0 and clamped to ground. They do not represent measured tower heights or building elevations.

Open the KMZ in Google Earth Pro, or import it into Earth web. Use Earth imagery, its terrain and available historical imagery for human review. Polygon holes and multipolygons are preserved in the export. This is an Earth overlay/review workflow, not an embedded Google Earth tile layer or automatic Earth elevation/imagery ingestion. Bring independently verified findings back through candidate, infrastructure or exclusion inputs.

Official sources:

- OpenStreetMap data and attribution: https://www.openstreetmap.org/copyright
- OSM tile policy: https://operations.osmfoundation.org/policies/tiles/
- Google Open Buildings formats, limitations and licenses: https://sites.research.google/gr/open-buildings/
- Google Maps API setup: https://developers.google.com/maps/documentation/javascript/get-api-key
- Google Maps map types: https://developers.google.com/maps/documentation/javascript/maptypes
- Earth imports: https://developers.google.com/maps/documentation/earth/import-data

## Start on Windows

1. Extract the entire ZIP into a writable folder, such as `C:\NewSite_Planning_Tool`.
2. Ensure Python with Tkinter is installed. Python 3.12/3.13 is a practical choice. Double-click **Start_Planner.bat**.
3. The first launch creates `.venv` and installs `numpy`, `pandas` and `scipy`; later launches reuse it. Your IT-approved package mirror can be used if corporate access restricts downloads. Keep certificate verification enabled.

Manual launch, from Command Prompt in the extracted folder:

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

From an Anaconda environment with those packages and Tkinter, run `python app.py`. The ZIP contains source and a launcher, not a Windows executable. Version 3 also requires Shapely and pyproj; the launcher installs them when missing.

## First trial with your current files

1. Under **Data sources**, keep the three bundled files selected.
2. In **Planning rules**, choose NG, 1km, grid snapshot `Sep'26`, Airtel snapshot `Sep_2026`, **MR preview**, minimum density 0 and maximum sites 30.
3. Under **Location search**, choose **Area search**, spacing 250 m and distance-decay strength 0.8. Leave infrastructure limits at 0 until reference data is loaded.
4. Click **Run planning**. Open **Results** and the interactive offline map. The result table includes latitude, longitude, coordinate basis and displacement from the grid centroid.
5. Each run creates a separate folder under `outputs`. Inspect `candidate_location_assessment.csv` for all alternatives and rejection reasons.

The example in `example_outputs/NG_1km_buildings_MR_preview` includes the real local OSM footprint evidence. The earlier no-building comparison selected 30 locations from 249,339 alternatives; 25 were away from the parent grid centroid. This is an **MR proxy demonstration**, not a population-based or field-approved site plan. A centroid may still win when it best serves the available demand; the method does not force an arbitrary displacement.

## Location methods

| Method | Candidate coordinates | Appropriate interpretation |
| --- | --- | --- |
| Area search | Multiple points across each target grid's nominal square plus its centre | Desktop shortlist; unknown physical feasibility |
| Supplied candidates | Exact tower, rooftop or parcel coordinates in your candidate CSV | Selection from your explicitly feasible inventory; RF/acquisition validation still required |
| Grid centroid (legacy) | Original grid centre | Baseline comparison only |

Area search spacing defaults to 250 m. Finer spacing explores more alternatives; it does not establish that coordinates are accurate to that spacing. The search uses nominal 1 km/3 km north/east extents and spherical direct coordinate offsets, since actual grid polygons are absent. Candidate count is capped at 500,000 by default (editable up to 1,000,000), and candidate-demand links are capped at 10 million to bound memory. Increase spacing or reduce scope if a limit is exceeded.

## Choosing actual candidate coordinates

1. Prepare feasible towers, rooftops or parcel access points in QGIS or your site-acquisition system. Spatially match each to the selected country/grid resolution. Check water, protected land, property restrictions and available access in that workflow.
2. Fill `data/siting_templates/candidate_locations_template.csv` using real coordinates. Do not use the grid centroid as a placeholder for a surveyed candidate.
3. Load it into **Data sources → Candidates**. Use **Inspect / map** to select alternate column names under **Field mappings**.
4. Choose **Location search → Supplied candidates** and set your spacing/access/backhaul rules.
5. Run and inspect the coordinate source, survey status, supplied accuracy and exclusion reports. The engine never silently falls back to centroids in this mode.

Required candidate columns:

| Column | Meaning |
| --- | --- |
| opco | Country code matching the selected grids |
| candidate_id | Unique candidate ID within country |
| grid_id | Parent grid ID in the selected country/resolution/snapshot |
| latitude, longitude | Actual WGS84 coordinate in decimal degrees |
| feasible | Must explicitly be `Yes`, `True` or `1` to participate |

Optional columns:

| Column | Meaning |
| --- | --- |
| suitability | 0–1 multiplier for your independently assessed site suitability; blank defaults to 1 |
| survey_verified | Your supplied survey status; not an assertion made by the application |
| accuracy_m | Supplied horizontal measurement accuracy in metres; blank remains unknown |
| location_source | Survey reference, tower inventory, rooftop survey or other evidence description |

Candidates may sit in any known grid in the selected scope, including a Green grid that can serve nearby Red/Amber demand. They must pass the configured site-present, spacing and other checks. A nominal parent-grid radius detects gross spatial-join mismatches; it does not prove containment in actual/clipped grid polygons. Multiple candidate IDs at the same coordinate compete for the same demand; new-site spacing prevents duplicate builds.

## Population and fine demand

No population is present in the three supplied files. For population planning:

1. Select country, resolution and grid snapshot.
2. Use **Create population template** on Data sources. Populate its blank values with real grid-level population.
3. Load the completed Population CSV, choose **Population** demand mode and select **People per grid** or **People per km2** to match the values.

Population schema: `opco,grid_id,population`. Join keys are country + exact grid ID. The same population field may represent density only when **People per km2** is explicitly selected. Nominal areas of 1 km²/9 km² are used; clipped/coastal grid area is not inferred. Unknown/negative/non-numeric values are excluded and disclosed; zero demand is not a target. Duplicate population/grid keys stop the run.

For better within-grid siting, load **Demand points** using `demand_points_template.csv`:

| Column | Meaning |
| --- | --- |
| opco, point_id, grid_id | Country, unique point ID and parent grid |
| latitude, longitude | Finer WGS84 demand location, such as a population tile centre |
| demand | People in Population mode; MR records in MR preview mode |

For each represented eligible grid, the sum of point demand must match the grid's demand within 0.0001% relative tolerance. A grid's fine demand **replaces**, rather than adds to, its coarse demand representation. No arbitrary population rescaling or uniform redistribution is applied. Coordinate and total errors stop the run. Grids with no detailed points retain a single coarse representation at the grid centre, clearly identified in outputs.

A grid can have demand points assigned to several sites. Each point is assigned once. `grid_assessment.csv` reports assigned demand, fraction, site count and site IDs. An unserved part is retained as unassigned; the whole grid population is not claimed merely because one fine point is within range. In MR preview, MR records are not population, unique users, traffic or revenue.

## No-build and infrastructure inputs

**No-build buffers**: `opco,exclusion_id,latitude,longitude,radius_m`. Each row is a circular exclusion zone; a candidate on or inside its radius is rejected. Radius must be finite and positive. Invalid constraint coordinates/radii stop the run. Load actual protected/water/land polygons and road alignments in GIS when preparing feasible candidates; this CSV workflow does not ingest polygons.

**Infrastructure points**: `opco,reference_id,kind,latitude,longitude`, where `kind` is `Road`, `Backhaul` or `Power` (case-insensitive). These are your sampled access, fiber/transport handoff or power reference locations.

Under Location search, set the maximum distances you want enforced. **0 disables a check.** A positive limit with no matching valid reference points stops planning. Distances are straight-line distances to reference **points**, not road/fiber route lengths, nearest positions on unsampled line geometry, microwave line-of-sight, available capacity, electrical feasibility or access rights. All three distances are exported, including unknown values when reference data is absent.

## Scoring and constraints

1. Scope the grid and Airtel snapshots to one country and one resolution. Only Red/Amber grids with valid positive demand contribute, subject to the density filter. Default combined category is `combined_category_wf`; use Field mappings to select `combined_category` if that is the approved classification. The tool does not infer the business meaning of `wf` or recalculate classifications from raw speeds.
2. Active 4G/5G Airtel sites block new candidates within the minimum distance. If reservation is enabled, planned/under-deployment sites also block locations. Legacy/off-air sites are visible for upgrade/reactivation review.
3. Generate or load candidate coordinates. **Recompute nearest-site and infrastructure distances at those exact coordinates.** Enforce confirmed feasibility for supplied candidates, source grid flags, no-build buffers and enabled infrastructure limits before ranking.
4. At each eligible demand point, apply Red/Amber severity (defaults 1.0/0.6) and competitor factor (default 1.25 inside 1.5 km of a 4G/5G competitor).
5. For a candidate within the assumed service radius, score each unassigned demand point as:

   `demand × severity × competitor factor × exp(-distance_decay × (distance / radius)²) × candidate suitability`

6. Select the largest marginal score; assign its unassigned in-radius demand points once; exclude other candidates within new-site spacing; recompute marginal scores and repeat. Minimum incremental demand uses raw people/MR, not the weighted score. Stop at the site limit or when no viable marginal demand remains.

Distance decay makes nearer demand matter more and helps differentiate alternative locations. It is a prioritization assumption, not a path-loss/throughput model. Setting decay to 0 removes it. Greedy selection does not guarantee a global mathematical optimum. Ties resolve deterministically from source/grid ordering; input candidates should be kept in consistent order for comparisons.

## Outputs

| File | Contents |
| --- | --- |
| proposed_sites.csv / proposed_sites.geojson | Selected coordinates, source candidate ID, coordinate basis, displacement, provenance, supplied survey accuracy/status, demand and nearest-site/infrastructure context |
| candidate_location_assessment.csv | Every valid-coordinate alternative, rejection reason, initial score and selected site ID |
| demand_point_assessment.csv | Every fine/coarse demand representation, unit-dependent value and assignment |
| demand_point_assignments.csv | Assigned points and exact site-to-demand distance |
| site_grid_assignments.csv | Per-site/per-grid assigned demand and point count; safe aggregation across split grids |
| grid_assessment.csv | Grid demand, assigned fraction and site count, coarse-site review context |
| optimization_review.csv | Target grids flagged at coarse screening for optimization/upgrade/pipeline review |
| data_quality_issues.csv | Invalid source values/coordinates and excluded/ambiguous competitor records, with source rows |
| airtel_sites_used.csv / competitor_sites_used.csv | Inventory used and historical source dates |
| planning_map.html | Offline map with approximate nominal cells, proposed locations, inventories and supplied fine demand points |
| run_settings.json | Rules, summary, source filenames and SHA-256 hashes |

A valid-coordinate candidate that fails a constraint remains in the alternative assessment. Invalid candidate coordinates are in the quality report instead. The map has no online basemap; displayed cells are not actual survey boundaries. Planning CSVs remain local. Online map and footprint services receive the viewed/requested geographic area; API keys are entered only in the browser session.

## Source coverage and inventory quality

Your bundled sources contain 92,604 grid records, 46,898 Airtel records and 66,391 competitor records. Grids cover 13 countries; Seychelles has Airtel sites but no grid input. Competitor dates are January–May 2026, older than the grid/Airtel September snapshot. Historical competitor presence is not evidence of current coverage, availability or quality.

The competitor file needs cp1252 fallback decoding. Missing/placeholder competitor IDs are excluded. Repeated identical records keep the first; conflicting coordinates under one country/operator/site ID are all excluded and audited. One snapshot per operator is required. Airtel and grid identifier conflicts stop planning rather than choosing arbitrary records.

## Scenarios and headless use

Save/Load scenario preserves paths, column mappings, rules and output folder. Update paths after moving computers. From the extracted folder, run the bundled MR preview without the GUI:

```bat
.venv\Scripts\python planner.py --config preview_NG.json
```

## What establishes a build location

Accurate physical coordinates require a trustworthy survey or inventory. A deployment-ready choice additionally needs land permission, buildability, terrain/clutter analysis, frequency/antenna/link-budget design, interference/capacity evaluation, feasible transport and power, and field verification. The application accepts and checks selected evidence inputs but does not perform these engineering surveys, raster analysis, RF propagation or land approval. Generating additional decimal places does not establish accuracy.

## Verification

Tests cover the legacy planner plus alternative search, supplied-coordinate preservation, distances, candidate feasibility, no-build buffers, infrastructure limits, fine-demand reconciliation, split/partial grid accounting, suitability ranking and search limits. See `VALIDATION.md` and `tests/real_data_checks.json`. Native Windows UI and browser visual interaction remain to be exercised locally.

```text
python -m unittest discover -s tests -v
python -m py_compile app.py planner.py siting.py buildings.py map_report.py web_maps.py
```

Official implementation references: [Tkinter](https://docs.python.org/3/library/tkinter.html), [SciPy cKDTree](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.cKDTree.query_ball_point.html).
