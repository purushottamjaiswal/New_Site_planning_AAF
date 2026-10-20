# New Site Planning v7.0

## Start and migrate

Extract the new package into a new folder. On Windows, run `Start_Planner.bat`; this release adds rasterio for DEM support. Load a previous scenario if needed, inspect the selected sources/layers and save it again in the new scenario format. Run/export again to obtain the corrected assignments, explanations, GeoPackage, persistent project and review controls. Existing HTML exports are not upgraded in place.

The separate QGIS plugin is v2.0. It uses a generated copy of the same v7 production engine, verified against `core_manifest.json`. Older v1 QGIS packages retain different limits and checks and should be replaced. Native QGIS GUI and Windows/provider interaction still require validation on the deployment machine.

## Required inputs

Grid, Airtel inventory and competitor tables remain required; Population mode additionally needs actual population keyed to grids. Land and water polygon datasets covering the planning area are required in every production mode, including demo. Use actual grid polygons where available. CSV-centred grids remain nominal reconstructions.

Only selected-country rows are staged in the working input database; grid resolution/period and Airtel period are also filtered. CSV sources still need a sequential scan, including an encoding validation pass. Building imports are clipped to the selected grid extent plus a margin, intersected with any supplied building clip box. Do not assume that bounding-box overlap establishes building or water completeness.

Detailed demand points must lie inside or on their actual parent grid polygon, including its holes, and totals must reconcile to parent demand. Shared-boundary points retain the explicitly supplied parent ID and are counted once. An incorrect parent mapping stops the run with its point/grid IDs.

## Planning policies

Choose these independently under Planning rules:

| Setting | Meaning |
|---|---|
| Target demand: Red + Amber / Red only | Determines which grid demand enters scoring. |
| Location: Current behaviour | Preserves prior generated-target/supplied-candidate behaviour. |
| Location: Target grids only | Every candidate must belong to an eligible target grid. |
| Location: Red grids only | Every candidate must be located in a selected Red grid polygon. |
| Location: Any selected grid | Locations in Green/other selected grids may serve nearby target demand. |
| Priority: Weighted demand | Maximizes remaining weighted opportunity at each greedy step. |
| Priority: Red demand first | Compares remaining weighted Red benefit first, then overall weighted benefit. It does not itself require placement inside Red. |

For a strict Red-location demonstration, use **Red only**, **Red grids only**, and **Red demand first**. For demand optimization across Red/Amber areas, choose the corresponding broader policies explicitly. The requested site total is an upper limit, not a guaranteed quota.

## Location methods

- **Quick demo**: clear Enable detailed location search; one approximate centroid per eligible location grid. Search-alternatives cap is ignored. Physical screening remains enabled.
- **Area search**: regular alternatives at the configured spacing; a preflight reports the generated count and stops early if it exceeds the cap.
- **Settlement search**: seeds alternatives from up to eight high-demand detailed points and mapped building-cluster means in four parent-grid quadrants, then checks nearby offsets. With no detailed/building evidence, a clearly labelled coarse-centroid fallback is used. This is desktop evidence, not identification of vacant land from imagery.
- **Supplied candidates**: feasible locations from an actual inventory; `feasible=Yes` is required. This is also the method for calibrated RF footprint matching with stable candidate IDs.

Rooftop search uses actual parent-polygon joins; footprint interiors remain structurally unverified. A DEM/road/clutter check cannot establish ownership, access rights or a structural certificate.

## Explain selections and alternatives

`proposed_sites.csv` includes incremental Red and Amber demand, weighted Red benefit, policy, score explanation, review status and engineering evidence. `candidate_location_assessment.csv` retains all captured rejection reasons and a primary rejection. `site_grid_assignments.csv` now groups by actual parent grid IDs, including multiple detailed demand points.

`alternative_candidates.csv` contains up to three same-parent-grid alternatives scored at the moment of the original selection, from at most 50 screened comparisons. These are bounded local comparisons, not a claim of a globally optimal plan. In the map, select/find a proposed site and click Show this site's alternatives. Choosing one fills the draft coordinates; recheck it against current fixed sites before saving.

## Interactive draft review

1. Find or select a proposed site. Leave Original site ID blank to create a new draft candidate instead.
2. Click Pick draft location on map, or enter WGS84 latitude/longitude.
3. Click Check draft / compare demand. Inspect failures, warnings and the change in incremental demand relative to the original site, holding other planned assignments fixed.
4. Enter reviewer and evidence/notes, select a status, and Save draft review.
5. Load review history to inspect revisions. Coordinates, original position, status, reviewer, timestamp, notes and checks are retained in the project database.

Positive reviews require passed automated checks; Rejected drafts may record failed checks. Missing building/DEM evidence blocks Field verified/Acquisition approved statuses. Those statuses record human declarations; the software does not conduct a survey or acquisition approval. Moving a site invalidates its original calibrated RF footprint.

**Saving a draft does not change the official plan or its assignments.** `reviewed_candidates.csv` is an audit/history export with `feasible=No`. Select the desired latest revisions, independently confirm physical feasibility, explicitly set Yes where justified, and rerun using Supplied candidates to issue a new official plan. Reject or omit superseded revisions.

## Persistent project and GIS outputs

When Save a persistent derived-results project is checked, `planning_project.sqlite` retains derived grids, candidates, demand/assignments, constraints, inventories, engineering evidence and reviews. Raw staged `inputs` are removed. A used building index is copied as a derived result so later map/review queries can work. This uses additional disk space; it does not keep the national working tables in RAM.

Full CSV and GeoJSON exports remain available. The optional `planning_results.gpkg` contains sites, grids and inventories with spatial indexes, numeric analytical fields and preserved text IDs. Earth KML/KMZ exports remain available.

After closing the planning application, run `Open_Result_Map.bat` in the exported result folder to reopen its durable result map. On other platforms: `python /path/to/review_project.py --folder /path/to/result_folder`. The launcher depends on the installed application/Python location. Do not move the result folder or runtime without updating its paths. DEMs remain external references; restore them before terrain-backed engineering approval. Local map serving chooses a free port if 8765 is occupied. Reopening a project does not require reimporting the original CSVs.

The initial browser preview and viewport caps remain bounded. The local result server provides full ID searches; an unserved HTML file alone retains only its embedded preview. Provider imagery/tiles require their normal connectivity and access. Offline canvas does not request external tiles.

## Engineering evidence

Optional Data sources:

- `road_lines`, `backhaul_lines`, `power_lines`: valid line/multiline geometry with a declared CRS. Enabled proximity limits accept line or point references. Line distances are local straight-line proximity, not a route, usable fiber connection or power-tap approval. With no distance limit, displayed line proximity searches are bounded to 10 km.
- `terrain`: a DEM TIFF with a declared horizontal CRS and elevations in metres. Local 3x3 elevation differences give a slope proxy. Nodata/edge locations fail screening when a DEM is loaded. Set a project-approved slope limit. A DSM may include vegetation/buildings; this is not propagation modelling.
- `rf_coverage`: coverage polygons with `candidate_id`, `calibration_id`, `validation_date` (YYYY-MM-DD), and `validated=Yes`. These are user-declared, externally validated footprints. For matched candidates, demand must lie inside the footprint and within the assumed maximum radius. Required RF mode rejects candidates without matching evidence and requires Supplied candidates. No propagation calibration is fitted automatically from these polygons.

Optional ground compound radius and water setback use conservative circular geometry. The compound must fit within one verified land polygon and avoid supplied water/no-build geometry; split land tiles can require merging before use. Building clearance is at least the compound radius. Road-line setback includes compound radius. These tests do not certify a rectangular lease plot, road right-of-way, slope stability or flood immunity.

## Scale controls and validation

Building batch rows remain configurable up to 400,000. Adaptive imports start smaller for GDAL datasets and adjust toward the configured MiB target; JSON chunks also consider estimated geometry/property size. This is a soft working-data target, not a hard cap on total process memory or a guarantee for one exceptionally large geometry. General input batches remain separate.

A synthetic trial selected/exported 10,050 sites from 50,250 area-search alternatives, staged only 20,101 selected-scope input rows from over one million source rows, and peaked at about 236 MiB on Linux. It used sparse demand and a 150 m assumed service radius; it is not a dense Nigeria performance promise. Source, constraints and real 1.5 km neighbourhoods can change runtime substantially.

Automated validation covers maintenance regressions, policies, durable projects, draft review, line proximity, DEM input and RF masks. CI configuration adds Linux/Windows unit checks and a real Chromium offline-map smoke test. Native QGIS GUI and corporate-provider browser behaviour remain deployment checks. No current feature establishes survey-accurate coordinates or calibrated RF performance without corresponding evidence.
