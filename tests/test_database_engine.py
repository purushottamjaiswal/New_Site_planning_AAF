import sys,tempfile,json,csv,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from planner import Rules,run_paths,plan_frames,save_plan
from session_db import Session
from test_planner import fixtures
from mask_fixtures import run_paths
from test_siting import candidates
from stream_io import stream_source,source_layers

class DatabaseTests(unittest.TestCase):
    def files(self,root,raw):
        paths={}
        for key,frame in raw.items():
            if key.startswith('_'):continue
            p=root/(key+'.csv');frame.to_csv(p,index=False);paths[key]=str(p)
        return paths
    def test_global_greedy_matches_reference_and_streams_exports(self):
        for mode in ('Grid centroid (legacy)','Area search'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                root=Path(td);raw=fixtures();rules=Rules(location_mode=mode,max_sites=20,min_new_km=2.,service_radius_km=1.1,distance_decay=0)
                reference=plan_frames(raw,rules,log=lambda s:None);result=run_paths(self.files(root,raw),rules,log=lambda s:None)
                try:
                    self.assertEqual(result.summary['target_demand_total'],reference.summary['target_demand_total'])
                    self.assertAlmostEqual(result.summary['incremental_demand_total'],reference.summary['incremental_demand_total'])
                    self.assertEqual(len(result.sites),len(reference.sites))
                    folder=save_plan(result,root/'out');sites=pd.read_csv(folder/'proposed_sites.csv')
                    self.assertEqual(len(sites),len(result.sites));self.assertTrue((folder/'planning_openlayers_map.html').exists())
                    dbpath=result.session.path;self.assertTrue(dbpath.exists())
                    self.assertEqual(result.session.count('inputs'),13)
                    self.assertEqual(result.session.count('inputs',"country<>'NG'"),0)
                finally:result.close()
                self.assertFalse(dbpath.exists())
    def test_supplied_green_parent_and_reconciled_fine_demand(self):
        with tempfile.TemporaryDirectory() as td:
            raw=fixtures();raw['candidates']=candidates([['NG','green','d',0,.059,'Yes'],['NG','left','b',0,.008,'Yes']]);raw['demand_points']=pd.DataFrame({'opco':['NG','NG'],'point_id':['p1','p2'],'grid_id':['e','e'],'latitude':[0,0],'longitude':[.068,.071],'demand':[100,200]})
            rules=Rules(location_mode='Supplied candidates',service_radius_km=1.5,max_sites=3)
            old=plan_frames(raw,rules,log=lambda s:None);new=run_paths(self.files(Path(td),raw),rules,log=lambda s:None)
            try:
                self.assertAlmostEqual(old.summary['incremental_demand_total'],new.summary['incremental_demand_total'])
                self.assertEqual(new.session.count('demand',"json_extract(payload,'$.point_id')='p1'"),1)
            finally:new.close()
    def test_duplicates_and_bad_fine_totals_fail(self):
        with tempfile.TemporaryDirectory() as td:
            raw=fixtures();raw['population']=pd.concat([raw['population'],raw['population'].iloc[[0]]]);paths=self.files(Path(td),raw)
            with self.assertRaisesRegex(ValueError,'duplicate'):run_paths(paths,Rules(),log=lambda s:None)
    def test_population_template_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            raw=fixtures();raw['population']=raw['population'].rename(columns={'opco':'Country','grid_id':'GridID','population':'nga_general_2020_sum'})
            result=run_paths(self.files(Path(td),raw),Rules(),log=lambda s:None)
            try:self.assertEqual(result.summary['target_demand_total'],1100)
            finally:result.close()
    def test_invalid_accuracy_and_zero_suitability_screening(self):
        with tempfile.TemporaryDirectory() as td:
            raw=fixtures();raw['candidates']=candidates([['NG','bad','b',0,.008,'Yes']]);raw['candidates']['accuracy_m']='wrong'
            result=run_paths(self.files(Path(td),raw),Rules(location_mode='Supplied candidates'),log=lambda s:None)
            try:self.assertEqual(len(result.sites),0);self.assertIn('accuracy',result.candidate_assessment.head().rejection.iloc[0])
            finally:result.close()
    def test_chunked_json_records_and_csv_without_reader_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'table.json';path.write_text(json.dumps([{'id':str(i)} for i in range(301)]),encoding='utf-8')
            pieces=list(stream_source(path,batch_size=100));self.assertEqual([len(p.frame) for p in pieces],[100,100,100,1])
            path=root/'table.csv';pd.DataFrame({'id':range(301)}).to_csv(path,index=False)
            self.assertEqual(sum(len(p.frame) for p in stream_source(path,batch_size=100)),301)
    def test_cancellation_cleans_session(self):
        with tempfile.TemporaryDirectory() as td:
            from unittest.mock import patch
            raw=fixtures();paths=self.files(Path(td),raw);seen=[]
            original=Session.close
            def close(s):seen.append(s.path);original(s)
            with patch.object(Session,'close',close),self.assertRaises(InterruptedError):run_paths(paths,Rules(),cancelled=lambda:True,log=lambda s:None)
            self.assertTrue(seen);self.assertTrue(all(not p.exists() for p in seen))
    def test_readonly_map_endpoint_queries_temporary_planning_rows(self):
        from web_maps import LocalMapServer
        import urllib.request
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);result=run_paths(self.files(root,fixtures()),Rules(location_mode='Grid centroid (legacy)'),log=lambda s:None)
            server=None
            try:
                folder=save_plan(result,root/'out');server=LocalMapServer(folder)
                with urllib.request.urlopen('http://127.0.0.1:8765/api/planning?layer=sites&bbox=-1,-1,2,1') as response:payload=json.load(response)
                self.assertEqual(len(payload['features']),len(result.sites));self.assertFalse(payload['truncated'])
            finally:
                if server:server.close()
                result.close()

    def test_rules_allow_large_requested_site_count(self):
        Rules(max_sites=25000,max_candidate_points=2000000).validate()
        with self.assertRaises(ValueError):Rules(max_sites=0).validate()

if __name__=='__main__':unittest.main()
