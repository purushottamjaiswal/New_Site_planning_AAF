# Version 6.1: water protection and complete grid footprints

## Before the next run

The prior version could propose a point in an ocean or lagoon because imagery was only a visual background. Version 6.1 requires two polygon sources. Existing plans need a new run to apply these checks.

1. In **Data sources**, select **Land polygons**: a coastal dry-land footprint containing the actual coast and islands. A general national administrative boundary can include inland water and is insufficient on its own.
2. Select **Water polygons**: river areas, lakes, reservoirs, lagoons and any other excluded water surfaces covering the planning scope. Include the Lagos lagoon for Lagos planning.
3. In **Source layers / CRS**, select each role, inspect its layers and choose the correct polygon layer. The source must declare its actual CRS, or your GIS owner must provide the correct override.
4. Keep the normal grid, Airtel, competitor and population inputs and rules. Building footprints remain optional and have their separate 400,000-row import setting.
5. Save the updated scenario and run again. A missing, empty, invalid or non-polygon land/water layer stops the run. No unchecked fallback to centroid placement occurs.

Land and water sources are independent of the grid. The tool never derives a water mask from Red/Amber categories, population, building absence, Google map colours or a screenshot.

## What the check does

Every generated, supplied and rooftop candidate must lie in the interior of a supplied land polygon. A point inside or on the boundary of a water polygon is rejected, even when a broad land polygon also contains it. Polygon holes preserve islands. No-build polygons and circular exclusion buffers still apply. Water screening remains mandatory for command-line runs as well as the desktop.

The checks use exact point-in-polygon topology against the imported vector geometry. They do not certify source completeness, present-day shoreline accuracy, tides, flooding, bridge suitability or a surveyed setback. A river represented only by a centre line does not establish its width. Ask GIS support for river-area polygons or buffers based on known bank/width evidence. For a conservative setback, include the required buffer in the excluded water polygons.

Prefer regional GPKG extracts with the correct CRS and retained polygon detail. Split/overlapping coastal land tiles can be more efficient than a single continent-scale polygon. Keep an overlap when tiling land so an artificial tile seam does not remove a real land candidate. Clip to the planning area with a margin, preserving coastlines, polygon holes and islands. Do not simplify away narrow rivers or small lagoons.

## Open sources for the GIS owner

- Coastal land polygons: https://osmdata.openstreetmap.de/data/land-polygons.html
- Inland waterbody datasets: https://osmdata.openstreetmap.de/data/water.html
- Coastline-derived ocean/sea polygons: https://osmdata.openstreetmap.de/data/water-polygons.html

The ocean/sea dataset does **not** include all lakes, reservoirs or inland rivers. It cannot replace an inland-water source. Check mapping completeness, source dates, licence and attribution before using any extract. This package does not bundle or automatically download national/global terrain masks.

## Grid footprints in every viewer

- A polygon grid source (GPKG, SHP, GeoJSON, etc.) retains its supplied boundaries. The tool preserves the grid IDs and source geometry.
- A CSV with centroid coordinates and a 1km/3km grid size cannot restore the original grid origin, projection, rotation or topology. Version 6.1 constructs nominal WGS84 ellipsoidal squares around those source centres. The `geometry_basis` column identifies this reconstruction. It does not move centres or fill gaps between source rows.
- To reproduce an original grid exactly, supply its actual polygon layer with the required grid attributes, rather than only a centroid CSV.
- `grid_assessment_spatial.geojson` contains every assessed grid footprint, including CSV-derived cells. `grid_assessment.csv` contains the corresponding provenance and demand/status fields.
- Offline and OpenLayers viewers use the same stored polygon geometry. Grids are visible by default in OpenLayers.
- The offline viewer opens on the local server and uses no external basemap scripts or imagery. Panning and zooming retrieve the current area from the temporary database while the app is open.
- Initial saved previews remain bounded. A live viewport can show up to 5,000 features per layer and reports truncation. Zoom in for more detail. The full export has every grid and site. Closing the app removes its working database and leaves the saved preview and complete exports available.

## Review the output

`candidate_location_assessment.csv` records the water-screening status and exclusion reason. Selected sites carry `water_screening` in CSV/GeoJSON. The output folder also contains `land_polygons_used.geojson` and `water_polygons_used.geojson`, source fingerprints and selected layer/CRS metadata. Preserve these with the plan for review.

A candidate rejected for water is removed from selection. Its demand can still contribute to another feasible site under the chosen planning radius. The application does not move all such points to the closest riverbank or promise that any replacement point is buildable.

The earlier v6.0 user guide and presentation remain useful for the rest of the workflow. This addendum takes precedence for required land/water inputs and grid/map behaviour.
