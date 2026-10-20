import sys,json,tempfile,unittest,csv
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from shapely.geometry import Polygon,mapping
from buildings import load_buildings,BuildingContext
from building_index import DiskBuildingContext
from planner import Rules

class DiskIndexTests(unittest.TestCase):
    def source(self,root):
        p=root/'buildings.geojson'
        polygons=[Polygon([(.0098,-.0002),(.0102,-.0002),(.0102,.0002),(.0098,.0002)]),Polygon([(5,5),(5.001,5),(5.001,5.001),(5,5.001)])]
        features=[{'type':'Feature','properties':{'building_id':str(i),'confidence':.9},'geometry':mapping(g)} for i,g in enumerate(polygons)]
        p.write_text(json.dumps({'type':'FeatureCollection','features':features+[features[0]]}),encoding='utf-8');return p,features
    def test_building_read_batch_is_separate_and_saved_in_metadata(self):
        from unittest.mock import patch
        from building_stream import building_chunks
        rules=Rules()
        self.assertEqual(rules.building_import_batch_size,400000)
        self.assertEqual(rules.import_batch_size,2000)
        rules.validate()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,_=self.source(root)
            with patch('building_stream.building_chunks',wraps=building_chunks) as reader:
                ctx=load_buildings(p,rules,disk=True,cache_dir=root/'cache')
            self.assertEqual(reader.call_args.kwargs['chunk_size'],400000)
            self.assertEqual(ctx.metadata['import_batch_rows'],400000)
            self.assertEqual(ctx.count,2)
            self.assertEqual(len(list(ctx.iter_features())),2)
        for value in (99,400001,True,400000.0):
            with self.subTest(value=value),self.assertRaises(ValueError):
                Rules(building_import_batch_size=value).validate()
        Rules(building_import_batch_size=100).validate()

    def test_disk_dedupe_large_extent_cache_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,features=self.source(root);cache=root/'cache'
            ctx=load_buildings(p,Rules(),disk=True,cache_dir=cache)
            self.assertIsInstance(ctx,DiskBuildingContext);self.assertEqual(len(ctx.features),2)
            self.assertEqual(len(list(ctx.iter_features((0,-1,1,1)))),1)
            self.assertEqual(len(ctx.features[:1]),1)
            again=load_buildings(p,Rules(),disk=True,cache_dir=cache);self.assertEqual(ctx.path,again.path)
            out=root/'export.geojson';ctx.export_geojson(out);self.assertEqual(len(json.loads(out.read_text(encoding='utf-8'))['features']),2)
    def test_disk_screening_matches_regional_and_crosses_tile_edge(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,features=self.source(root);ctx=load_buildings(p,Rules(),disk=True,cache_dir=root/'cache')
            regional=BuildingContext(features[:1],{})
            frame=pd.DataFrame({'lon':[.01,.012,.0999,4.9999],'lat':[0,0,0,5.0002],'rejection':['']*4})
            for rule in ('Context only','Ground clearance','Rooftop search'):
                rules=Rules(building_rule=rule);actual=frame.copy();ctx.annotate(actual,rules)
                expected=frame.iloc[:3].copy();regional.annotate(expected,rules)
                self.assertEqual(actual.iloc[:3].mapped_buildings_within_radius.tolist(),expected.mapped_buildings_within_radius.tolist())
                self.assertEqual(actual.iloc[:3].rejection.tolist(),expected.rejection.tolist())
                self.assertAlmostEqual(actual.iloc[0].mapped_footprint_area_m2,expected.iloc[0].mapped_footprint_area_m2,delta=.1)
                self.assertEqual(actual.iloc[3].mapped_buildings_within_radius,1)
    def test_cache_changes_with_filter_and_cancellation_leaves_no_partial(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,_=self.source(root)
            a=load_buildings(p,Rules(),disk=True,cache_dir=root/'cache')
            b=load_buildings(p,Rules(building_min_confidence=.8),disk=True,cache_dir=root/'cache');self.assertNotEqual(a.path,b.path)
            count=[0]
            def cancel():count[0]+=1;return count[0]>2
            with self.assertRaises(InterruptedError):load_buildings(p,Rules(),disk=True,cache_dir=root/'cancelled',cancelled=cancel)
            self.assertFalse(list((root/'cancelled').glob('*.partial')))
    def test_geojson_type_after_features_is_streamed(self):
        from building_stream import building_layers
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,features=self.source(root)
            p.write_text(json.dumps({'features':features,'type':'FeatureCollection'}),encoding='utf-8')
            self.assertEqual(building_layers(p)[0]['name'],'buildings')
            ctx=load_buildings(p,Rules(),disk=True,cache_dir=root/'cache')
            self.assertEqual(ctx.count,2);self.assertEqual(ctx.metadata['driver'],'Streaming GeoJSON')

    def test_streamed_projected_esri_json_and_hole(self):
        from building_stream import building_layers
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'esri.json'
            p.write_text(json.dumps({'geometryType':'esriGeometryPolygon','spatialReference':{'wkid':3857},'features':[{'attributes':{'building_id':'E1'},'geometry':{'rings':[[[1000,0],[1100,0],[1100,100],[1000,100],[1000,0]],[[1020,20],[1040,20],[1040,40],[1020,40],[1020,20]]]}}]}),encoding='utf-8')
            self.assertEqual(building_layers(p)[0]['name'],'esri')
            ctx=load_buildings(p,Rules(),disk=True,cache_dir=root/'cache')
            feature=ctx.features[0];self.assertLess(feature['geometry']['coordinates'][0][0][0],.02)
            self.assertEqual(len(feature['geometry']['coordinates']),2)
            self.assertEqual(ctx.metadata['driver'],'Streaming Esri JSON')

    def test_map_endpoint_and_requested_openlayers_tiles(self):
        import urllib.request
        from urllib.error import HTTPError
        from planner import plan_frames,save_plan
        from test_planner import fixtures
        from web_maps import LocalMapServer
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source,_=self.source(root);ctx=load_buildings(source,Rules(),disk=True,cache_dir=root/'cache')
            raw=fixtures();raw['_buildings']=ctx;plan=plan_frames(raw,Rules(),log=lambda s:None);plan.input_metadata={'buildings':ctx.metadata}
            folder=save_plan(plan,root/'out');html=(folder/'planning_openlayers_map.html').read_text(encoding='utf-8')
            self.assertIn('var wms_layers = []',html);self.assertIn('mt1.google.com/vt/lyrs=s',html);self.assertIn('mt1.google.com/vt/lyrs=m',html)
            server=LocalMapServer(folder)
            try:
                with urllib.request.urlopen(server.url) as response:self.assertEqual(response.status,200)
                with urllib.request.urlopen('http://127.0.0.1:8765/api/buildings?bbox=0,-0.1,0.1,0.1') as response:data=json.load(response)
                self.assertEqual(len(data['features']),1);self.assertFalse(data['truncated'])
                with self.assertRaises(HTTPError):urllib.request.urlopen('http://127.0.0.1:8765/api/buildings?bbox=0,0,90,90')
            finally:server.close()

    def test_multiple_streamed_batches_aggregate_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'many.csv'
            with p.open('w',newline='') as f:
                writer=csv.writer(f);writer.writerow(['building_id','geometry'])
                for i in range(1005):
                    x=.01+i*.0000001;writer.writerow([str(i),Polygon([(x,0),(x+.00000002,0),(x+.00000002,.00000002),(x,.00000002)]).wkt])
            ctx=load_buildings(p,Rules(),disk=True,cache_dir=root/'cache')
            frame=pd.DataFrame({'lon':[.01],'lat':[0.0],'rejection':['']});ctx.annotate(frame,Rules())
            self.assertEqual(frame.mapped_buildings_within_radius.iloc[0],1005)

if __name__=='__main__':unittest.main()
