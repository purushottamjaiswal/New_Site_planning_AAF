## v6.2 quick demo validation

All 76 automated tests passed on Linux. Regression checks cover one candidate per target grid, bypassing a candidate limit of 1 in demo mode, enforcing the limit again in detailed mode, continued rejection of water centroids, approximate-location metadata, agreement with the reference planner, and restoring detailed UI settings after toggling. UI rule extraction is tested with Tcl variables without a display; the new checkbox has not been interactively verified on Windows.

# Validation for version 6.1

- **73 automated tests passed**, including missing/empty/non-polygon water evidence, ocean and coastline rejection, lagoon/river boundary exclusion, island holes, nominal 1 km/3 km dimensions, complete grid GeoJSON and identical live endpoint geometry.
- Native Tk resizing and 10,050-row result paging smoke check passed under a virtual Linux display with the new required inputs. Actual Windows DPI interaction was not tested.
- The offline viewer JavaScript ran in a mocked DOM/canvas harness and successfully loaded/drew local grid/site viewport data. OpenLayers and alternate viewer scripts passed syntax checks. A browser binary was unavailable for live browser rendering, so the JavaScript harness does not establish actual browser rendering compatibility.
- A fresh synthetic scale run with the required mask checks selected/exported all **10,050** eligible sites from a **12,000** upper target. It imported **1,010,051** population rows, peaked at **183.27 MiB** Linux process RSS and completed in **35.17 s** including fixture creation/export. Full grid polygons were exported and session cleanup passed. These are fixture-specific measurements. Evidence: `tests/water_grid_scale_check.json`.
- New runs require user-supplied coastal land and inland-water polygon evidence. Tests use explicitly synthetic geometry and do not certify the completeness or accuracy of Nigeria terrain data.
- Supplied grid polygons are preserved. CSV-only grids produce nominal ellipsoidal 1 km/3 km footprints with geometry provenance, rather than a claim to recover original grid topology.

## Earlier version 6.0 measurements

- Automated regression suite: **66 tests passed**. Includes the earlier planner/format checks, plus temporary database ingestion, global-selection agreement with the small-fixture reference engine, fine-demand reconciliation, explicit population-template aliases, invalid-accuracy screening, cancellation cleanup, streamed outputs and the read-only planning-map endpoint.
- Synthetic scale trial: **1,010,051 population-source rows**, **1,020,103 stored input rows**, 10,050 target grids, request for 12,000 sites. The engine selected and fully exported **10,050 sites**, exhausted the available eligible demand and correctly stopped below the requested cap. Page offset 10,000 returned the remaining 50 rows.
- Measured Linux peak process RSS: **148.71 MiB**, with a **316,014,592-byte** SQLite database and **31.55 seconds** including fixture generation, import, selection and export. Import batches were 2,000 rows. The session database was removed after closing the result. These are fixture/environment-specific observations, not memory/speed guarantees for other inputs. Evidence: `tests/database_scale_check.json`.
- Native Tk smoke check under a virtual Linux display: Run controls remained visible at 1280×900, 1200×650, 1000×560, 800×480 and 680×420 at two Tk scaling settings. A 10,050-row result was paginated into 200-row pages with 50 rows on its last page, and application close removed its temporary session. No Tk callback errors occurred. Evidence: `tests/ui_resize_check.json`.
- Full user-data trial was performed locally with supplied grid/population and site files. Operational inputs/outputs are excluded from this public source repository.
- Static/browser previews are bounded and explicitly labelled. Full selected-site and footprint exports stream without truncating result counts. Live viewport queries require the active session; normal app close removes it.
- Actual Windows DPI/taskbar behavior and live browser rendering remain unverified. Individual geometry complexity, GDAL driver internals, Python/UI batches and geometry calculations contribute memory beyond the SQLite cache. Large plans also require sufficient temporary and output disk capacity.
- The small-fixture `plan_frames` helper is retained only as an in-memory regression reference; normal desktop and command-line planning use the disk-backed engine.
