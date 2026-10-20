import sys,json,tempfile,gzip,csv,io,unittest,xml.etree.ElementTree as ET,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd
from shapely.geometry import Polygon,mapping,Point
from newsite_planning_qgis.engine.buildings import BuildingContext,load_buildings,overpass_to_features,area_bbox
from newsite_planning_qgis.engine.planner import Rules,plan_frames,save_plan
from test_planner import fixtures
from test_siting import candidates


def footprint(geometry=None,confidence=.9):
    if geometry is None:geometry=Polygon([(.0098,-.0002),(.0102,-.0002),(.0102,.0002),(.0098,.0002),(.0098,-.0002)])
    return {'type':'Feature','geometry':mapping(geometry),'properties':{'building_id':'b1','confidence':confidence,'source':'Synthetic unit-test footprint','license':'Test data'}}

class BuildingTests(unittest.TestCase):
    def ctx(self):return BuildingContext([footprint()],{'source':'Synthetic unit-test footprint'})
    def plan(self,rule='Ground clearance'):
        raw=fixtures();raw['_buildings']=self.ctx();raw['candidates']=candidates([['NG','on_roof','b',0,.01,'Yes'],['NG','clear','b',0,.012,'Yes']])
        rules=Rules(location_mode='Supplied candidates',building_rule=rule,max_sites=1,min_new_km=.1,service_radius_km=1.5)
        return plan_frames(raw,rules,log=lambda s:None)

    def test_ground_rule_excludes_footprint(self):
        p=self.plan();self.assertEqual(p.sites.source_candidate_id.tolist(),['clear'])
        c=p.candidate_assessment.set_index('source_candidate_id');self.assertIn('mapped footprint',c.loc['on_roof','rejection'])
        self.assertGreater(c.loc['clear','mapped_buildings_within_radius'],0)

    def test_roof_rule_requires_real_polygon_and_reports_area(self):
        p=self.plan('Rooftop search');self.assertEqual(p.sites.source_candidate_id.tolist(),['on_roof'])
        self.assertEqual(p.sites.containing_building_id.iloc[0],'b1')
        self.assertGreater(p.sites.containing_building_area_m2.iloc[0],100)

    def test_context_boost_and_no_population_inference(self):
        p=self.plan('Context only');self.assertGreater(p.sites.building_factor.iloc[0],1)
        self.assertEqual(p.summary['target_demand_total'],1100)

    def test_roof_generation_preserves_hole(self):
        outer=[(.006,-.002),(.01,-.002),(.01,.002),(.006,.002),(.006,-.002)]
        hole=[(.007,-.001),(.009,-.001),(.009,.001),(.007,.001),(.007,-.001)]
        poly=Polygon(outer,[hole]);ctx=BuildingContext([footprint(poly)],{'source':'Synthetic'})
        raw=fixtures();raw['_buildings']=ctx
        p=plan_frames(raw,Rules(building_rule='Rooftop search',location_mode='Area search',max_sites=1),log=lambda s:None)
        self.assertEqual(len(p.sites),1);row=p.sites.iloc[0];self.assertTrue(poly.covers(Point(row.lon,row.lat)))
        self.assertIn('structurally unverified',row.location_basis)

    def test_roof_missing_footprints_fails(self):
        with self.assertRaisesRegex(ValueError,'requires building footprints'):
            plan_frames(fixtures(),Rules(building_rule='Rooftop search'),log=lambda s:None)

    def test_confidence_and_invalid_polygon_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'buildings.geojson';path.write_text(json.dumps({'type':'FeatureCollection','features':[footprint(confidence=.5)]}))
            with self.assertRaisesRegex(ValueError,'No usable building'):load_buildings(path,Rules())
            path.write_text(json.dumps({'type':'FeatureCollection','features':[footprint(),{'type':'Feature','geometry':{'type':'Point','coordinates':[.01,0]},'properties':{}}]}))
            ctx=load_buildings(path,Rules());self.assertEqual(len(ctx.features),1);self.assertEqual(len(ctx.issues),1)

    def test_google_wkt_gzip_streaming(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'buildings.csv.gz'
            with gzip.open(path,'wt',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['latitude','longitude','geometry','confidence','full_plus_code']);w.writeheader();w.writerow({'latitude':0,'longitude':.01,'geometry':Polygon([(.009,-.001),(.011,-.001),(.011,.001),(.009,.001)]).wkt,'confidence':.9,'full_plus_code':'TESTCODE'})
            ctx=load_buildings(path,Rules(building_clip_bbox='-0.1,-0.1,0.1,0.1'))
            self.assertEqual(len(ctx.features),1);self.assertEqual(ctx.ids,['TESTCODE'])

    def test_overpass_way_and_relation_holes(self):
        coords=lambda xy:[{'lon':x,'lat':y} for x,y in xy]
        outer=[(0,0),(.01,0),(.01,.01),(0,.01),(0,0)]
        inner=[(.002,.002),(.008,.002),(.008,.008),(.002,.008),(.002,.002)]
        data={'elements':[{'type':'relation','id':1,'tags':{'building':'yes'},'members':[
          {'type':'way','role':'outer','geometry':coords(outer)}, {'type':'way','role':'inner','geometry':coords(inner)}]}]}
        f,bad=overpass_to_features(data);self.assertEqual(len(f),1);self.assertEqual(bad,0);self.assertEqual(len(f[0]['geometry']['coordinates']),2)
        with self.assertRaisesRegex(ValueError,'incomplete'):overpass_to_features({'remark':'timeout'})

    def test_earth_export_and_web_providers(self):
        p=self.plan('Context only')
        with tempfile.TemporaryDirectory() as td:
            f=save_plan(p,td);tree=ET.parse(f/'planning_google_earth.kml');ns={'k':'http://www.opengis.net/kml/2.2'}
            self.assertGreater(len(tree.findall('.//k:Placemark',ns)),0)
            with zipfile.ZipFile(f/'planning_google_earth.kmz') as z:self.assertIn('doc.kml',z.namelist())
            page=(f/'planning_web_map.html').read_text();self.assertIn('maps.googleapis.com/maps/api/js',page);self.assertIn('https://tile.openstreetmap.org/',page)
            self.assertNotIn('mt1.google.com',page);self.assertTrue((f/'building_footprints_used.geojson').exists())

    def test_clip_uses_polygon_not_csv_centroid_hint(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'footprints.csv'
            with path.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['latitude','longitude','geometry','confidence']);w.writeheader()
                w.writerow({'latitude':3,'longitude':3,'geometry':Polygon([(.009,-.001),(.011,-.001),(.011,.001),(.009,.001)]).wkt,'confidence':.9})
            ctx=load_buildings(path,Rules(building_clip_bbox='-0.002,0.0095,0.002,0.0105'))
            self.assertEqual(len(ctx.features),1)

    def test_large_import_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'footprints.geojson';path.write_text(json.dumps({'type':'FeatureCollection','features':[footprint() for _ in range(1000)]}))
            with self.assertRaises(InterruptedError):load_buildings(path,Rules(),cancelled=lambda:True)

    def test_download_bbox_is_bounded(self):
        box=area_bbox(6.5,3.4,1);self.assertEqual(len(box),4)
        with self.assertRaises(ValueError):area_bbox(6.5,3.4,500)

if __name__=='__main__':unittest.main(verbosity=2)
