import sys,tempfile,json,zipfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pyogrio
from pyogrio import raw
from shapely.geometry import Polygon,Point,mapping
from gis_io import read_source,list_layers,normalize_role,match_mapping,driver_report
from buildings import load_buildings
from planner import Rules,ALIASES,run_paths,save_plan,source_fingerprint
from test_planner import fixtures
from mask_fixtures import run_paths
from test_siting import candidates

POLY=Polygon([(1000,-20),(1050,-20),(1050,20),(1000,20),(1000,-20)])

def write_vector(path,driver,layer='buildings',polygon=POLY,crs='EPSG:3857'):
    raw.write(str(path),geometry=np.array([polygon.wkb],dtype=object),field_data=[np.array(['building_1'],dtype=object),np.array([.9])],fields=['building_id','confidence'],layer=layer,driver=driver,geometry_type=polygon.geom_type,crs=crs)

KML='''<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Survey</name><Folder><name>Buildings</name><Placemark><name>Building A</name><ExtendedData><Data name="building_id"><value>building_1</value></Data><Data name="confidence"><value>0.9</value></Data></ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>0.009,-0.0002 0.0095,-0.0002 0.0095,0.0002 0.009,0.0002 0.009,-0.0002</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder></Document></kml>'''

class ImportTests(unittest.TestCase):
    def test_gpkg_multi_layer_selection_and_reprojection(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'layers.gpkg';write_vector(path,'GPKG');write_vector(path,'GPKG',layer='sites',polygon=Point(1000,0))
            names=[r['name'] for r in list_layers(path)];self.assertEqual(set(names),{'buildings','sites'})
            with self.assertRaisesRegex(ValueError,'Select a layer'):read_source(path)
            d=read_source(path,layer='buildings');self.assertEqual(d.geometries[0].geom_type,'Polygon');self.assertLess(d.geometries[0].centroid.x,.02)
            c=load_buildings(path,Rules(),layer='buildings');self.assertEqual(len(c.features),1);self.assertGreater(c.areas[0],1900)

    def test_shapefile_and_missing_crs_override(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'buildings.shp';write_vector(path,'ESRI Shapefile')
            self.assertEqual(len(read_source(path).frame),1);path.with_suffix('.prj').unlink()
            with self.assertRaisesRegex(ValueError,'missing its CRS'):read_source(path)
            d=read_source(path,crs_override='EPSG:3857');self.assertLess(d.geometries[0].centroid.x,.02)
            path.with_suffix('.dbf').unlink()
            with self.assertRaisesRegex(ValueError,'matching .dbf'):read_source(path)

    def test_gdb_and_direct_gdbtable(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'buildings.gdb';write_vector(path,'OpenFileGDB')
            d=read_source(path);self.assertEqual(len(d.frame),1);self.assertLess(d.geometries[0].centroid.x,.02)
            readable=[]
            for table in path.glob('*.gdbtable'):
                try:
                    info=pyogrio.read_info(str(table))
                    if info.get('geometry_type') in ('Polygon','MultiPolygon') and 'building_id' in info.get('fields',[]):readable.append(table)
                except Exception:pass
            self.assertTrue(readable,'Driver did not expose a spatial gdbtable fixture')
            self.assertEqual(len(read_source(readable[0]).frame),1)
            c=load_buildings(path,Rules());self.assertEqual(len(c.features),1)

    def test_kml_and_kmz_attributes_and_polygons(self):
        with tempfile.TemporaryDirectory() as td:
            kml=Path(td)/'buildings.kml';kml.write_text(KML,encoding='utf-8')
            kmz=Path(td)/'buildings.kmz'
            with zipfile.ZipFile(kmz,'w') as z:z.write(kml,'doc.kml')
            for path in (kml,kmz):
                d=read_source(path);self.assertEqual(d.frame.building_id.iloc[0],'building_1');self.assertEqual(d.geometries[0].geom_type,'Polygon')
                self.assertEqual(len(load_buildings(path,Rules()).features),1)

    def test_esri_json_projected_polygons(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'esri.json'
            obj={'geometryType':'esriGeometryPolygon','spatialReference':{'wkid':3857},'fields':[{'name':'building_id','type':'esriFieldTypeString'},{'name':'confidence','type':'esriFieldTypeDouble'}], 'features':[{'attributes':{'building_id':'b1','confidence':.9},'geometry':{'rings':[list(POLY.exterior.coords)]}}]}
            path.write_text(json.dumps(obj),encoding='utf-8');d=read_source(path);self.assertEqual(d.metadata['driver'],'ESRIJSON');self.assertLess(d.geometries[0].centroid.x,.02)
            self.assertEqual(len(load_buildings(path,Rules()).features),1)

    def test_zip_shapefile_and_gdb(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);shp=root/'buildings.shp';write_vector(shp,'ESRI Shapefile')
            archive=root/'shapes.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for p in root.glob('buildings.*'):z.write(p,p.name)
            self.assertEqual(len(read_source(archive).frame),1)
            gdb=root/'sample.gdb';write_vector(gdb,'OpenFileGDB');gdbzip=root/'sample.gdb.zip'
            with zipfile.ZipFile(gdbzip,'w') as z:
                for p in gdb.iterdir():z.write(p,'sample.gdb/'+p.name)
            self.assertEqual(len(read_source(gdbzip).frame),1)

    def test_json_table_and_csv_projected_coordinate_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'data.json';path.write_text(json.dumps([{'opco':'NG','grid_id':'001','population':100}]),encoding='utf-8')
            d=read_source(path);self.assertEqual(d.frame.grid_id.iloc[0],'001')
            frame=pd.DataFrame({'latitude':['0'],'longitude':['1000']});from gis_io import Dataset
            f,m,_=normalize_role(Dataset(frame),'airtel',{'lat':'latitude','lon':'longitude'},{'crs_override':'EPSG:3857'})
            self.assertAlmostEqual(float(f[m['lon']].iloc[0]),.00898315,places=6)

    def test_operator_alias_uses_values_to_avoid_country_operator_id(self):
        frame=pd.DataFrame({'Opco_Comp':['CD_Vodacom'],'OPCO_Comp_1':['Vodacom']})
        result,_=match_mapping(frame,'competitor',ALIASES['competitor'],ALIASES['competitor'])
        self.assertEqual(result['operator'],'OPCO_Comp_1')

    def test_native_integer_site_ids_do_not_gain_decimal_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ids.gpkg'
            raw.write(str(path),geometry=np.array([Point(0,0).wkb,Point(1,0).wkb],dtype=object),field_data=[np.array([1170,1171],dtype=np.int64)],fields=['SiteID'],driver='GPKG',layer='sites',geometry_type='Point',crs='EPSG:4326')
            d=read_source(path);self.assertEqual(d.frame.SiteID.tolist(),['1170','1171'])

    def test_spatial_grid_pipeline_uses_actual_boundary_and_area(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);data=fixtures();paths={}
            data['grid']=data['grid'].iloc[[0]].copy();data['population']=data['population'].iloc[[0]].copy()
            grid=root/'grid.gpkg';poly=Polygon([(0,-.002),(.002,-.002),(.002,.002),(0,.002)])
            attrs=data['grid'].drop(columns=['centroid_lat','centroid_long']);fields=list(attrs.columns)
            raw.write(str(grid),geometry=np.array([poly.wkb],dtype=object),field_data=[attrs[c].to_numpy(dtype=object) for c in fields],fields=fields,layer='kpi',driver='GPKG',geometry_type='Polygon',crs='EPSG:4326')
            paths['grid']=str(grid)
            for kind in ('airtel','competitor','population'):
                p=root/(kind+'.csv');data[kind].to_csv(p,index=False);paths[kind]=str(p)
            c=root/'candidates.csv';candidates([['NG','out','a',0,.003,'Yes'],['NG','in','a',0,.001,'Yes']]).to_csv(c,index=False);paths['candidates']=str(c)
            p=run_paths(paths,Rules(location_mode='Supplied candidates',max_sites=1),log=lambda s:None,source_options={'grid':{'layer':'kpi'}})
            self.assertEqual(p.sites.source_candidate_id.tolist(),['in']);self.assertLess(p.grids.area_km2.iloc[0],.2)
            folder=save_plan(p,root/'outputs',paths);self.assertTrue((folder/'grid_assessment_spatial.geojson').exists())

    def test_polygon_exclusion_boundary_and_hole(self):
        from constraints import PolygonConstraints
        polygon=Polygon([(0,0),(1,0),(1,1),(0,1)],holes=[[(.4,.4),(.6,.4),(.6,.6),(.4,.6)]])
        frame=pd.DataFrame({'grid_id':['a']*4,'lat':[.2,.5,0,2],'lon':[.2,.5,.2,2],'rejection':['']*4})
        PolygonConstraints(no_build_geometries=[polygon]).annotate(frame)
        self.assertEqual(frame.rejection.ne('').tolist(),[True,False,True,False])

    def test_driver_report_and_directory_manifest(self):
        self.assertIn('OpenFileGDB',driver_report()['drivers'])
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'dataset.gdb';path.mkdir();(path/'component').write_text('test',encoding='utf-8')
            self.assertIn('metadata manifest',source_fingerprint(path)['fingerprint_method'])

if __name__=='__main__':unittest.main(verbosity=2)
