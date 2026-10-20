"""Desktop environment and real numeric elevation raster integration tests."""
import sys,tempfile,unittest,zipfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import rasterio
from rasterio.transform import from_origin
from dem_io import inspect_dem,open_dem
from check_desktop_environment import check_environment
from engineering import Engineering
from session_db import Session
from planner import Rules,run_paths,save_plan
from test_planner import fixtures
from mask_fixtures import masks

class DesktopDemTests(unittest.TestCase):
    def raster(self,path,driver='GTiff',count=1,crs='EPSG:4326',unit='metre',scaled=False):
        values=np.full((6,6),100,dtype='int16')
        if scaled:values[:,1]=-30000;values[:,3]=30000
        with rasterio.open(path,'w',driver=driver,width=6,height=6,count=count,dtype='int16',crs=crs,transform=from_origin(.02,.015,.005,.005),nodata=-32768) as dem:
            for band in range(1,count+1):dem.write(values,band);dem.set_band_unit(band,unit)
            if scaled:dem.scales=(2.,);dem.offsets=(10.,)
        return path
    def test_complete_environment_includes_rasterio_and_geotiff_driver(self):
        report=check_environment()
        self.assertIn('rasterio',report['versions']);self.assertIn('GTiff',report['terrain_drivers'])
    def test_missing_rasterio_fails_environment_check_with_repair_instructions(self):
        import importlib
        original=importlib.import_module
        def load(name):
            if name=='rasterio':raise ModuleNotFoundError('No module named rasterio')
            return original(name)
        with patch('check_desktop_environment.importlib.import_module',side_effect=load):
            with self.assertRaisesRegex(RuntimeError,'Setup_Desktop_Environment.bat'):check_environment()
    def test_zip_geotiff_passes_through_planning_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dem=self.raster(root/'elevation.tif');archive=root/'nga_dem.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:z.write(dem,'elevation/nga.tif')
            paths={}
            for role,frame in fixtures().items():
                p=root/(role+'.csv');frame.to_csv(p,index=False);paths[role]=str(p)
            paths.update(masks(root));paths['terrain']=str(archive)
            result=run_paths(paths,Rules(location_mode='Quick demo (grid centroid)',terrain_required=True,max_slope_deg=10),log=lambda s:None)
            try:
                self.assertGreater(len(result.sites),0)
                self.assertEqual(result.sites.head().terrain_elevation_m.iloc[0],100)
                folder=save_plan(result,root/'outputs')
                metadata=json.loads((folder/'run_settings.json').read_text(encoding='utf-8'))['source_layers_and_crs']['terrain']
                self.assertEqual(metadata['driver'],'GTiff');self.assertIn('/vsizip/',metadata['raster_source'])
            finally:result.close()
    def test_erdas_img_inside_zip_is_read_as_elevation(self):
        with rasterio.Env() as env:
            if 'HFA' not in env.drivers():self.skipTest('HFA driver not provided by this rasterio build')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dem=self.raster(root/'nga_dem.img',driver='HFA');archive=root/'nga_dem.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                for p in root.glob('nga_dem.img*'):z.write(p,'elevation/'+p.name)
            info=inspect_dem(archive);self.assertEqual(info['driver'],'HFA')
            with open_dem(archive) as reader:self.assertEqual(reader.read(1,window=rasterio.windows.Window(2,2,1,1))[0,0],100)
    def test_ambiguous_empty_and_non_raster_archives_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dem=self.raster(root/'a.tif')
            for members in [[],['a.tif','b.tif']]:
                archive=root/'bad.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    for name in members:z.write(dem,name)
                with self.assertRaisesRegex(ValueError,'Extract it and select one DEM'):inspect_dem(archive)
            bad=root/'garmin.img';bad.write_bytes(b'This is not a numeric elevation raster')
            with self.assertRaisesRegex(ValueError,'Garmin map IMG'):inspect_dem(bad)
    def test_invalid_band_count_crs_and_units_fail_early(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for kwargs,message in [({'count':3},'one numeric elevation band'),({'crs':None},'actual horizontal CRS'),({'unit':'foot'},'units must be metres')]:
                p=self.raster(root/'bad.tif',**kwargs)
                with self.assertRaisesRegex(ValueError,message):inspect_dem(p)
    def test_scaled_int16_terrain_uses_float_arithmetic(self):
        with tempfile.TemporaryDirectory() as td:
            p=self.raster(Path(td)/'scaled.tif',scaled=True);session=Session();engineering=None
            try:
                engineering=Engineering(session,Rules(),str(p));elevation,slope=engineering.terrain(0,.0325)
                self.assertEqual(elevation,210.);self.assertGreater(slope,80.)
            finally:
                if engineering:engineering.close()
                session.close()
    def test_invalid_dem_stops_before_ingesting_national_tables(self):
        with patch('session_db.Session.ingest') as ingest:
            with self.assertRaisesRegex(ValueError,'Elevation Model file does not exist'):
                run_paths(dict(grid='grid.csv',airtel='sites.csv',competitor='comp.csv',land_polygons='land.gpkg',water_polygons='water.gpkg',terrain='missing-elevation.tif'),Rules(),log=lambda s:None)
            ingest.assert_not_called()

if __name__=='__main__':unittest.main()
