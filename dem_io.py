"""Validate numeric elevation rasters; access ZIP members without extracting into RAM."""
from pathlib import Path,PurePosixPath
from zipfile import ZipFile

RASTER_SUFFIXES={'.tif','.tiff','.img','.vrt','.asc','.dem','.hgt'}
DEM_FILE_TYPES=[('Elevation model','*.tif *.tiff *.img *.vrt *.asc *.dem *.hgt *.zip'),('All files','*.*')]

def open_dem(path):
    try:import rasterio
    except ImportError as exc:raise ValueError('Elevation Model needs rasterio. Run Setup_Desktop_Environment.bat and start with Start_Planner.bat.') from exc
    path=Path(path).expanduser().resolve()
    if not path.is_file():raise ValueError('Elevation Model file does not exist: '+str(path))
    source=str(path)
    if path.suffix.lower()=='.zip':
        with ZipFile(path) as archive:
            members=[i.filename for i in archive.infolist() if not i.is_dir() and
                     PurePosixPath(i.filename).suffix.lower() in RASTER_SUFFIXES and
                     '__MACOSX' not in PurePosixPath(i.filename).parts]
        if len(members)!=1:
            raise ValueError(f'Elevation ZIP contains {len(members)} supported raster files. Extract it and select one DEM raster; merge tiles into one raster first if necessary.')
        member=PurePosixPath(members[0])
        if member.is_absolute() or '..' in member.parts:raise ValueError('Invalid raster member path in Elevation ZIP.')
        source='/vsizip/'+path.as_posix()+'/'+members[0]
    try:dem=rasterio.open(source)
    except rasterio.errors.RasterioIOError as exc:
        raise ValueError('Cannot read the Elevation Model as a numeric raster. Use GeoTIFF or a GDAL-readable elevation IMG; Garmin map IMG files are not elevation rasters. For ZIP inputs, extract the raster and its sidecars if direct reading fails. '+str(exc)) from exc
    try:
        if not dem.crs:raise ValueError('DEM must declare its actual horizontal CRS.')
        if dem.count!=1:raise ValueError('Elevation Model must have one numeric elevation band, not an RGB image.')
        if dem.width<3 or dem.height<3:raise ValueError('DEM needs at least 3 x 3 pixels for slope screening.')
        if dem.dtypes[0].startswith('complex'):raise ValueError('DEM elevation values must be real numbers.')
        unit=(dem.units[0] or '').strip().lower()
        if unit and unit not in ('m','meter','meters','metre','metres'):raise ValueError('DEM elevation units must be metres. Convert this raster before planning: '+unit)
        return dem
    except BaseException:dem.close();raise

def describe_dem(dem,path):
    return {'source':str(path),'raster_source':dem.name,'driver':dem.driver,'width':dem.width,'height':dem.height,
            'source_crs':str(dem.crs),'band_count':dem.count,'dtype':dem.dtypes[0],
            'pixel_size_source_units':list(dem.res),'nodata':str(dem.nodata),
            'elevation_units':dem.units[0] or 'unspecified: source must use metres',
            'scale':dem.scales[0],'offset':dem.offsets[0],
            'storage':'windowed raster reads; source file remains external'}

def inspect_dem(path):
    with open_dem(path) as dem:return describe_dem(dem,path)
