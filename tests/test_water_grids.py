import sys,json,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shapely.geometry import Polygon,box,shape,mapping
from pyproj import Geod
from planner import Rules,run_paths,save_plan
from test_planner import fixtures
from test_siting import candidates
from mask_fixtures import masks
from terrain_masks import nominal_grid_polygon

class WaterGridTests(unittest.TestCase):
    def inputs(self,root,locations=None):
        raw=fixtures()
        if locations is not None:raw['candidates']=candidates(locations)
        out={}
        for role,frame in raw.items():
            p=root/(role+'.csv');frame.to_csv(p,index=False);out[role]=str(p)
        return out
    def test_required_masks_cannot_be_silently_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths=self.inputs(root)
            with self.assertRaisesRegex(ValueError,'Water protection requires'):run_paths(paths,Rules(),log=lambda s:None)
            paths.update(masks(root));paths.pop('water_polygons')
            with self.assertRaisesRegex(ValueError,'water_polygons'):run_paths(paths,Rules(),log=lambda s:None)
    def test_ocean_coast_lagoon_river_and_boundary_candidates_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            sites=[['NG','lagoon','b',0,.007,'Yes'],['NG','river-boundary','b',0,.008,'Yes'],['NG','dry','b',0,.010,'Yes'],['NG','sea','c',0,.03,'Yes'],['NG','coast','b',0,.012,'Yes']]
            paths=self.inputs(root,sites)
            water=Polygon([(.006,-.002),(.008,-.002),(.008,.002),(.006,.002)])
            paths.update(masks(root,land=box(-.1,-.1,.012,.1),water=water))
            result=run_paths(paths,Rules(location_mode='Supplied candidates',min_new_km=0,max_sites=10),log=lambda s:None)
            try:
                records=result.candidate_assessment.head(20).set_index('source_candidate_id')
                self.assertIn('water polygon',records.loc['lagoon','rejection'])
                self.assertIn('water polygon',records.loc['river-boundary','rejection'])
                self.assertIn('land boundary',records.loc['sea','rejection'])
                self.assertIn('land boundary',records.loc['coast','rejection'])
                self.assertEqual(result.sites.source_candidate_id.tolist(),['dry'])
                self.assertTrue(result.sites.head(20).water_screening.str.startswith('Passed').all())
                folder=save_plan(result,root/'out',paths)
                self.assertTrue((folder/'water_polygons_used.geojson').exists())
                self.assertTrue((folder/'land_polygons_used.geojson').exists())
            finally:result.close()
    def test_water_hole_allows_real_island_and_land_hole_blocks_lagoon(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths=self.inputs(root,[['NG','island','b',0,.008,'Yes']])
            water=Polygon([(.005,-.004),(.011,-.004),(.011,.004),(.005,.004)],holes=[[ (.007,-.001),(.009,-.001),(.009,.001),(.007,.001)]])
            paths.update(masks(root,water=water))
            result=run_paths(paths,Rules(location_mode='Supplied candidates'),log=lambda s:None)
            try:self.assertEqual(result.sites.source_candidate_id.tolist(),['island'])
            finally:result.close()
            land=Polygon([(-.1,-.1),(.1,-.1),(.1,.1),(-.1,.1)],holes=[[(.007,-.001),(.009,-.001),(.009,.001),(.007,.001)]])
            paths.update(masks(root,land=land))
            result=run_paths(paths,Rules(location_mode='Supplied candidates'),log=lambda s:None)
            try:self.assertEqual(len(result.sites),0)
            finally:result.close()
    def test_empty_water_and_line_only_water_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths={**self.inputs(root),**masks(root)};p=Path(paths['water_polygons'])
            p.write_text(json.dumps({'type':'FeatureCollection','features':[]}))
            with self.assertRaises(ValueError):run_paths(paths,Rules(),log=lambda s:None)
            p.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'LineString','coordinates':[[0,0],[1,1]]}}]}))
            with self.assertRaises(ValueError):run_paths(paths,Rules(),log=lambda s:None)
    def test_nominal_metric_square_at_nigeria_latitude(self):
        geod=Geod(ellps='WGS84')
        for side in (1000,3000):
            poly=nominal_grid_polygon(6.5,3.4,side);points=list(poly.exterior.coords)
            for a,b in zip(points,points[1:]):self.assertAlmostEqual(geod.inv(*a,*b)[2],side,delta=.001)
            self.assertTrue(poly.contains(__import__('shapely').Point(3.4,6.5)))
    def test_all_csv_grids_export_polygons_and_live_endpoint_retains_them(self):
        from web_maps import LocalMapServer
        import urllib.request
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths={**self.inputs(root),**masks(root)}
            result=run_paths(paths,Rules(),log=lambda s:None);server=None
            try:
                folder=save_plan(result,root/'out',paths)
                features=json.loads((folder/'grid_assessment_spatial.geojson').read_text())['features']
                self.assertEqual(len(features),5)
                self.assertTrue(all(f['geometry']['type']=='Polygon' for f in features))
                server=LocalMapServer(folder)
                with urllib.request.urlopen('http://127.0.0.1:8765/api/planning?layer=grids&bbox=-1,-1,1,1') as response:data=json.load(response)
                self.assertEqual([f['geometry'] for f in data['features']],[f['geometry'] for f in features])
                html=(folder/'planning_map.html').read_text();self.assertIn('scheduleViewport',html);self.assertIn('/api/planning?layer=',html)
                self.assertIn('geometry_basis',features[0]['properties'])
            finally:
                if server:server.close()
                result.close()

if __name__=='__main__':unittest.main()
