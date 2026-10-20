"""Validate the desktop planner's interpreter, including rasterio and raster drivers."""
import importlib
import importlib.metadata
import json
import sys

PACKAGES=('numpy','pandas','scipy','shapely','pyproj','pyogrio','ijson','rasterio')

def check_environment():
    versions={};errors=[]
    for name in PACKAGES:
        try:module=importlib.import_module(name)
        except Exception as exc:
            errors.append(f'{name}: {exc}');continue
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=getattr(module,'__version__',None)
    drivers={}
    if not errors:
        try:
            import rasterio
            with rasterio.Env() as env:drivers=env.drivers()
            if 'GTiff' not in drivers:errors.append('rasterio has no GeoTIFF driver')
        except Exception as exc:errors.append(f'rasterio/GDAL drivers: {exc}')
    if errors:
        raise RuntimeError('Desktop environment is incomplete:\n'+'\n'.join(errors)+
            '\nRun Setup_Desktop_Environment.bat, then Start_Planner.bat. '+
            'For this interpreter use: "'+sys.executable+'" -m pip install -r requirements.txt')
    return {'python':sys.executable,'versions':versions,'rasterio_gdal':rasterio.__gdal_version__,
            'terrain_drivers':{name:drivers[name] for name in ('GTiff','HFA','VRT','AAIGrid','SRTMHGT') if name in drivers}}

if __name__=='__main__':
    try:print(json.dumps(check_environment(),indent=2));sys.exit(0)
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
