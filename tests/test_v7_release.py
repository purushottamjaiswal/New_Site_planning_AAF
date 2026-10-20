"""Independent regression tests for correctness, policy and project-review changes."""
import sys,json,tempfile,unittest,sqlite3
from pathlib import Path
from types import SimpleNamespace
from dataclasses import fields,asdict
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from shapely.geometry import box,mapping
from planner import Rules,run_paths,save_plan
from test_planner import fixtures
from test_siting import candidates
from mask_fixtures import masks

class ReleaseTests(unittest.TestCase):
    def files(self,root,raw=None):
        paths={}
        for role,frame in (raw or fixtures()).items():
            p=root/(role+'.csv');frame.to_csv(p,index=False);paths[role]=str(p)
        return {**paths,**masks(root)}
    def polygon_file(self,root,name,features):
        p=root/(name+'.geojson');p.write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8');return str(p)
    def test_assignment_export_aggregates_real_parent_grid_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['demand_points']=pd.DataFrame({'opco':['NG','NG'],'point_id':['p1','p2'],'grid_id':['e','e'],'latitude':[0,0],'longitude':[.068,.071],'demand':[100,200]})
            result=run_paths(self.files(root,raw),Rules(location_mode='Quick demo (grid centroid)',min_new_km=0,max_sites=20),log=lambda s:None)
            try:
                folder=save_plan(result,root/'out');assign=pd.read_csv(folder/'site_grid_assignments.csv')
                self.assertTrue(set(assign.grid_id)<=set(result.grids.id.tolist()))
                self.assertAlmostEqual(assign.assigned_demand.sum(),result.summary['incremental_demand_total'])
                e=assign[assign.grid_id=='e'];self.assertEqual(e.assigned_demand.sum(),300);self.assertEqual(e.assigned_points.sum(),2)
            finally:result.close()
    def test_actual_polygon_contains_demand_and_accepts_shared_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['demand_points']=pd.DataFrame({'opco':['NG'],'point_id':['outside'],'grid_id':['b'],'latitude':[0],'longitude':[.011],'demand':[200]});paths=self.files(root,raw)
            features=[]
            for row in raw['grid'].to_dict('records'):
                x=float(row['centroid_long']);features.append({'type':'Feature','properties':row,'geometry':mapping(box(x-.001,-.001,x+.001,.001))})
            paths['grid']=self.polygon_file(root,'actual_grids',features)
            with self.assertRaisesRegex(ValueError,'outside its parent grid polygon'):run_paths(paths,Rules(),log=lambda s:None)
            raw['demand_points']['longitude']=.009;raw['demand_points'].to_csv(paths['demand_points'],index=False)
            result=run_paths(paths,Rules(),log=lambda s:None)
            try:self.assertEqual(result.session.count('demand',"json_extract(payload,'$.point_id')='outside'"),1)
            finally:result.close()
    def test_red_first_and_red_only_are_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['population'].loc[raw['population'].grid_id=='b','population']='2000';paths=self.files(root,raw)
            settings=dict(location_mode='Quick demo (grid centroid)',max_sites=1,min_airtel_km=0,min_new_km=0,service_radius_km=.05)
            for priority,expected in [('Weighted demand','b'),('Red demand first','c')]:
                result=run_paths(paths,Rules(**settings,priority_policy=priority),log=lambda s:None)
                try:
                    self.assertEqual(result.sites.head().grid_id.iloc[0],expected)
                    self.assertIn(priority,result.sites.head().selection_explanation.iloc[0])
                finally:result.close()
            result=run_paths(paths,Rules(**settings,target_policy='Red only'),log=lambda s:None)
            try:self.assertEqual(result.session.count('demand',"json_extract(payload,'$.category')='Amber'"),0)
            finally:result.close()
    def test_strict_red_locations_and_settlement_seeds(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['candidates']=candidates([['NG','amber','b',0,.008,'Yes'],['NG','red','c',0,.03,'Yes']]);paths=self.files(root,raw)
            result=run_paths(paths,Rules(location_mode='Supplied candidates',location_policy='Red grids only'),log=lambda s:None)
            try:self.assertIn('disallowed',result.candidate_assessment.head().set_index('source_candidate_id').loc['amber','rejection'])
            finally:result.close()
            raw.pop('candidates');raw['demand_points']=pd.DataFrame({'opco':['NG'],'point_id':['fine'],'grid_id':['c'],'latitude':[.001],'longitude':[.03],'demand':[500]})
            result=run_paths(self.files(root,raw),Rules(location_mode='Settlement search'),log=lambda s:None)
            try:self.assertTrue(result.candidate_assessment.head(100).location_basis.str.contains('Detailed demand seed').any())
            finally:result.close()
    def test_persistent_project_review_survives_close_and_preserves_plan(self):
        from project_review import ProjectSession,evaluate,save_review
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=run_paths(self.files(root),Rules(location_mode='Quick demo (grid centroid)'),log=lambda s:None)
            folder=save_plan(result,root/'out');original=result.sites.head().iloc[0].to_dict();result.close();db=folder/'planning_project.sqlite'
            project=ProjectSession(db)
            try:
                self.assertIsNone(project.db.execute("SELECT name FROM sqlite_master WHERE name='inputs'").fetchone())
                request={'site_id':original['candidate_id'],'lat':original['lat']+.00001,'lon':original['lon'],'reviewer':'Planner','notes':'Desktop imagery review; survey pending','status':'Imagery reviewed'}
                preview=evaluate(project,request);self.assertTrue(preview['checks_passed'],preview)
                record=save_review(project,request);self.assertEqual(record['revision'],1)
                unchanged=json.loads(project.db.execute('SELECT payload FROM sites WHERE gid=?',(original['candidate_id'],)).fetchone()[0]);self.assertEqual(unchanged['lat'],original['lat'])
                self.assertEqual(pd.read_csv(folder/'reviewed_candidates.csv').feasible.iloc[0],'No')
                request['lat']=91
                with self.assertRaises(ValueError):save_review(project,request)
            finally:project.close()
    def test_network_lines_and_required_rf_evidence(self):
        from shapely.geometry import LineString
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['candidates']=candidates([['NG','onroad','c',0,.03,'Yes'],['NG','access','c',0,.0305,'Yes'],['NG','far','c',0,.032,'Yes']]);paths=self.files(root,raw)
            paths['road_lines']=self.polygon_file(root,'roads',[{'type':'Feature','properties':{},'geometry':mapping(LineString([(.03,-.01),(.03,.01)]))}])
            result=run_paths(paths,Rules(location_mode='Supplied candidates',max_road_km=.1,service_radius_km=.2),log=lambda s:None)
            try:
                frame=result.candidate_assessment.head().set_index('source_candidate_id');self.assertIn('road-line setback',frame.loc['onroad','rejection']);self.assertEqual(frame.loc['access','rejection'],'');self.assertTrue(frame.loc['far','rejection'])
            finally:result.close()
            paths.pop('road_lines');paths['rf_coverage']=self.polygon_file(root,'rf',[{'type':'Feature','properties':{'candidate_id':'access','calibration_id':'FIELD_TEST_1','validation_date':'2026-10-20','validated':'Yes'},'geometry':mapping(box(.029,-.001,.031,.001))}])
            result=run_paths(paths,Rules(location_mode='Supplied candidates',rf_required=True),log=lambda s:None)
            try:self.assertEqual(result.sites.source_candidate_id.tolist(),['access']);self.assertIn('Imported validated',result.sites.head().rf_evidence.iloc[0])
            finally:result.close()
    def test_dem_sampling_and_missing_required_dem(self):
        import rasterio,numpy as np
        from rasterio.transform import from_origin
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths=self.files(root)
            with self.assertRaisesRegex(ValueError,'supply a DEM'):run_paths(paths,Rules(terrain_required=True),log=lambda s:None)
            dem=root/'dem.tif'
            with rasterio.open(dem,'w',driver='GTiff',width=6,height=6,count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(.02,.015,.005,.005),nodata=-9999) as f:f.write(np.ones((6,6),dtype='float32')*100,1)
            paths['terrain']=str(dem);result=run_paths(paths,Rules(location_mode='Quick demo (grid centroid)',terrain_required=True,max_slope_deg=10),log=lambda s:None)
            try:
                self.assertGreater(len(result.sites),0);self.assertEqual(result.sites.head().terrain_elevation_m.iloc[0],100)
            finally:result.close()
    def test_result_geopackage_preserves_layers_and_numeric_values(self):
        import pyogrio
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=run_paths(self.files(root),Rules(location_mode='Quick demo (grid centroid)'),log=lambda s:None)
            try:
                folder=save_plan(result,root/'out');gpkg=folder/'planning_results.gpkg'
                self.assertTrue({'sites','grids','airtel','competitors'}<=set(x[0] for x in pyogrio.list_layers(gpkg)))
                info=pyogrio.read_info(gpkg,layer='sites');self.assertEqual(info['features'],len(result.sites))
                self.assertIn('float',str(info['dtypes'][list(info['fields']).index('weighted_benefit')]))
            finally:result.close()
    def test_adaptive_json_buildings_yield_below_row_cap(self):
        from building_stream import building_chunks
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=self.polygon_file(root,'large_properties',[{'type':'Feature','properties':{'building_id':str(i),'description':'x'*100000},'geometry':mapping(box(.03,0,.0301,.0001))} for i in range(40)])
            pieces=list(building_chunks(path,chunk_size=400000,memory_mb=16))
            self.assertEqual(sum(len(p.frame) for p in pieces),40);self.assertGreater(len(pieces),1)
    def test_compound_water_intersection_rejects_dry_centre(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures();raw['candidates']=candidates([['NG','drycentre','c',0,.03,'Yes']]);paths=self.files(root,raw);paths.update(masks(root,water=box(.03005,-.001,.031,.001)))
            result=run_paths(paths,Rules(location_mode='Supplied candidates',site_compound_radius_m=10),log=lambda s:None)
            try:self.assertEqual(len(result.sites),0);self.assertIn('Compound/water',result.candidate_assessment.head().rejection.iloc[0])
            finally:result.close()
    def test_review_http_checks_origin_and_persists_after_close(self):
        import urllib.request,urllib.error
        from web_maps import LocalMapServer
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=run_paths(self.files(root),Rules(location_mode='Area search'),log=lambda s:None);folder=save_plan(result,root/'out');row=result.sites.head().iloc[0];result.close();server=LocalMapServer(folder)
            try:
                base=server.url.rsplit('/',1)[0];payload={'site_id':row.candidate_id,'lat':row.lat,'lon':row.lon,'reviewer':'Planner','notes':'Image reviewed','status':'Imagery reviewed'}
                def post(endpoint,origin=None):
                    headers={'Content-Type':'application/json'}
                    if origin:headers['Origin']=origin
                    req=urllib.request.Request(base+endpoint,data=json.dumps(payload).encode(),headers=headers)
                    with urllib.request.urlopen(req) as response:return json.load(response)
                self.assertTrue(post('/api/review/preview')['checks_passed']);self.assertEqual(post('/api/review/save')['revision'],1)
                with self.assertRaises(urllib.error.HTTPError) as error:post('/api/review/save','https://unrelated.example')
                self.assertEqual(error.exception.code,403)
                with urllib.request.urlopen(base+'/api/reviews') as response:self.assertEqual(len(json.load(response)),1)
                with urllib.request.urlopen(base+'/api/alternatives?site_id='+row.candidate_id) as response:self.assertLessEqual(len(json.load(response)['features']),3)
            finally:server.close()
    def test_derived_building_index_survives_working_session(self):
        from project_review import ProjectSession
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths=self.files(root);paths['buildings']=self.polygon_file(root,'buildings',[{'type':'Feature','properties':{'building_id':'b1'},'geometry':mapping(box(.0305,.0005,.0306,.0006))}])
            result=run_paths(paths,Rules(location_mode='Quick demo (grid centroid)',building_rule='Context only'),log=lambda s:None);folder=save_plan(result,root/'out');result.close();project=ProjectSession(folder/'planning_project.sqlite')
            try:self.assertIsNotNone(project.buildings);self.assertEqual(project.buildings.count,1)
            finally:project.close()

    def test_result_folder_can_move_and_reopen(self):
        import shutil,urllib.request
        from project_review import ProjectSession
        from web_maps import LocalMapServer
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=run_paths(self.files(root),Rules(location_mode='Quick demo (grid centroid)'),log=lambda s:None);folder=save_plan(result,root/'out');site=result.sites.head().candidate_id.iloc[0];result.close()
            self.assertTrue((folder/'Open_Result_Map.bat').read_text(encoding='utf-8').startswith('@echo off\n'));self.assertIn('--folder "%~dp0."',(folder/'Open_Result_Map.bat').read_text(encoding='utf-8'))
            moved=root/'moved_result';shutil.move(str(folder),moved);server=LocalMapServer(moved)
            try:
                base=server.url.rsplit('/',1)[0]
                with urllib.request.urlopen(base+'/api/search?id='+site) as response:self.assertEqual(len(json.load(response)['features']),1)
                project=ProjectSession(moved/'planning_project.sqlite');project.close()
            finally:server.close()

    def test_scenario_load_restores_mode_and_detailed_values(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as td:
            master=tk.Tcl();defaults=Rules();values={f.name:(tk.BooleanVar(master,value=getattr(defaults,f.name)) if isinstance(getattr(defaults,f.name),bool) else tk.StringVar(master,value=str(getattr(defaults,f.name)))) for f in fields(Rules)}
            holder=SimpleNamespace(busy=False,values=values,paths={},maps={},output=tk.StringVar(master),location_search_enabled=tk.BooleanVar(master,value=False),show_source_profile=lambda:None,update_location_search=lambda:None)
            file=Path(td)/'scenario.json';file.write_text(json.dumps({'rules':asdict(defaults)}),encoding='utf-8')
            with patch('app.filedialog.askopenfilename',return_value=str(file)):App.load_scenario(holder)
            self.assertEqual(App.get_rules(holder).location_mode,'Area search');self.assertTrue(holder.location_search_enabled.get())
            file.write_text(json.dumps({'rules':asdict(Rules(location_mode='Quick demo (grid centroid)')),'ui_state':{'location_search_enabled':False,'detailed_values':{'location_mode':'Supplied candidates','search_spacing_m':'500','max_candidate_points':'1000000','building_rule':'Rooftop search'}}}),encoding='utf-8')
            with patch('app.filedialog.askopenfilename',return_value=str(file)):App.load_scenario(holder)
            self.assertEqual(App.get_rules(holder).location_mode,'Quick demo (grid centroid)');holder.location_search_enabled.set(True);self.assertEqual(App.get_rules(holder).location_mode,'Supplied candidates');self.assertEqual(App.get_rules(holder).max_candidate_points,1000000)

if __name__=='__main__':unittest.main()
