import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import tempfile
import pandas as pd
import numpy as np
from planner import Rules,plan_frames,save_plan,nearest
from test_planner import fixtures


def candidates(rows):
    return pd.DataFrame(rows,columns=['opco','candidate_id','grid_id','latitude','longitude','feasible'])

class SitingTests(unittest.TestCase):
    def plan(self,raw=None,**kwargs):
        settings={'mode':'Population','location_mode':'Supplied candidates','service_radius_km':1.1,
         'min_airtel_km':.2,'min_new_km':.1,'max_sites':3};settings.update(kwargs)
        return plan_frames(raw or self.raw(),Rules(**settings),log=lambda s:None)
    def raw(self):
        raw=fixtures();raw['candidates']=candidates([['NG','parcel1','b',0,.01,'Yes'],['NG','parcel2','c',0,.032,'Yes']])
        return raw

    def test_exact_input_coordinates_preserved_and_distances_recalculated(self):
        raw=self.raw();raw['candidates']['accuracy_m']=['3','5'];raw['candidates']['survey_verified']='Yes'
        p=self.plan(raw)
        self.assertEqual(set(p.sites.source_candidate_id),{'parcel1','parcel2'})
        self.assertTrue(np.allclose(sorted(p.sites.lon),[.01,.032]))
        self.assertTrue((p.sites.offset_from_grid_centroid_m>100).all())
        self.assertTrue((p.sites.location_basis=='Supplied candidate coordinate').all())
        self.assertEqual(set(p.sites.accuracy_m),{3,5})
        for row in p.sites.to_dict('records'):
            dist,_=nearest(pd.DataFrame([row]),p.airtel.query("planning_role == 'Active 4G/5G'"))
            self.assertAlmostEqual(dist[0],row['nearest_airtel_km'])

    def test_missing_candidate_file_never_falls_back(self):
        with self.assertRaisesRegex(ValueError,'requires a candidate CSV'):self.plan(fixtures())

    def test_feasibility_and_parent_grid_envelope(self):
        raw=self.raw();raw['candidates'].loc[0,'feasible']='Unknown';raw['candidates'].loc[1,'longitude']=10.0
        p=self.plan(raw);self.assertEqual(len(p.sites),0)
        self.assertTrue(p.candidate_assessment.rejection.ne('').all())

    def test_no_build_buffer_excludes_exact_coordinates(self):
        raw=self.raw();raw['exclusions']=pd.DataFrame({'opco':['NG'],'exclusion_id':['wetland'],
             'latitude':[0],'longitude':[.01],'radius_m':[100]})
        p=self.plan(raw);self.assertNotIn('parcel1',p.sites.source_candidate_id.tolist())
        self.assertIn('No-build buffer',p.candidate_assessment.rejection.iloc[0])
        raw['exclusions']['radius_m']=raw['exclusions']['radius_m'].astype(str);raw['exclusions'].loc[0,'radius_m']='bad'
        with self.assertRaisesRegex(ValueError,'radius_m'):self.plan(raw)

    def test_road_reference_constraint_and_missing_data(self):
        with self.assertRaisesRegex(ValueError,'Road limit enabled'):self.plan(max_road_km=.2)
        raw=self.raw();raw['infrastructure']=pd.DataFrame({'opco':['NG'],'reference_id':['r1'],
          'kind':['Road'],'latitude':[0],'longitude':[.01]})
        p=self.plan(raw,max_road_km=.2);self.assertEqual(p.sites.source_candidate_id.tolist(),['parcel1'])
        self.assertAlmostEqual(p.sites.nearest_road_km.iloc[0],0)

    def test_detailed_demand_can_split_grid_without_double_count(self):
        raw=self.raw();raw['population'].loc[raw['population'].grid_id!='b','population']='0'
        raw['candidates']=candidates([['NG','west','b',0,.005,'Yes'],['NG','east','b',0,.011,'Yes']])
        raw['demand_points']=pd.DataFrame({'opco':['NG','NG'],'point_id':['p1','p2'],
          'grid_id':['b','b'],'latitude':[0,0],'longitude':[.005,.011],'demand':[80,120]})
        p=self.plan(raw,service_radius_km=.2,max_sites=2)
        self.assertEqual(len(p.sites),2);self.assertEqual(p.sites.incremental_demand.sum(),200)
        g=p.grids.set_index('id').loc['b'];self.assertEqual(g.assigned_site_count,2);self.assertEqual(g.assigned_fraction,1)
        assigned=p.demand_points.query("assigned_candidate != ''");self.assertEqual(len(assigned),2)
        self.assertTrue(assigned.point_id.is_unique)
        with tempfile.TemporaryDirectory() as td:
            folder=save_plan(p,td)
            agg=pd.read_csv(folder/'site_grid_assignments.csv');self.assertEqual(agg.assigned_demand.sum(),200)

    def test_partial_grid_population_is_not_all_claimed(self):
        raw=self.raw();raw['population'].loc[raw['population'].grid_id!='b','population']='0'
        raw['demand_points']=pd.DataFrame({'opco':['NG','NG'],'point_id':['p1','p2'],
          'grid_id':['b','b'],'latitude':[0,0],'longitude':[.005,.011],'demand':[80,120]})
        raw['candidates']=candidates([['NG','east','b',0,.011,'Yes']])
        p=self.plan(raw,service_radius_km=.2,max_sites=1)
        self.assertEqual(p.summary['incremental_demand_total'],120)
        self.assertEqual(p.grids.set_index('id').loc['b','assigned_fraction'],.6)
        self.assertEqual(p.summary['unassigned_target_grids'],1)

    def test_mismatched_detail_totals_fail(self):
        raw=self.raw();raw['demand_points']=pd.DataFrame({'opco':['NG'],'point_id':['p1'],
           'grid_id':['b'],'latitude':[0],'longitude':[.008],'demand':[999]})
        with self.assertRaisesRegex(ValueError,'must match grid demand'):self.plan(raw)

    def test_lattice_has_real_alternatives_not_just_centroids(self):
        p=self.plan(fixtures(),location_mode='Area search',search_spacing_m=250)
        self.assertGreater(len(p.candidate_assessment),10)
        self.assertTrue((p.candidate_assessment.offset_from_grid_centroid_m>100).any())
        self.assertTrue(p.sites.accuracy_m.isna().all())
        self.assertTrue((p.sites.location_status=='Desktop search point; physical feasibility unknown').all())

    def test_suitability_changes_ranking(self):
        raw=self.raw();raw['candidates']=candidates([['NG','low','b',0,.008,'Yes'],['NG','high','b',0,.0081,'Yes']])
        raw['candidates']['suitability']=[.01,1]
        p=self.plan(raw,max_sites=1);self.assertEqual(p.sites.source_candidate_id.iloc[0],'high')

    def test_actual_coordinate_not_parent_centroid_controls_spacing(self):
        raw=self.raw();raw['airtel']['longitude']=raw['airtel'].longitude.astype(float);raw['airtel'].loc[0,'longitude']=.008
        raw['candidates']=candidates([['NG','edge','b',0,.012,'Yes']])
        p=self.plan(raw,min_airtel_km=.3)
        self.assertEqual(p.sites.source_candidate_id.tolist(),['edge'])
        self.assertGreater(p.sites.nearest_airtel_km.iloc[0],.3)

    def test_candidate_in_green_grid_can_serve_red_amber_demand(self):
        raw=self.raw();raw['candidates']=candidates([['NG','green_parcel','d',0,.06,'Yes']])
        p=self.plan(raw,service_radius_km=1.2)
        self.assertEqual(p.sites.source_candidate_id.tolist(),['green_parcel'])
        self.assertEqual(p.sites.incremental_demand.iloc[0],300)
        self.assertEqual(p.sites.category.iloc[0],'Green')

    def test_search_limit_is_enforced(self):
        with self.assertRaisesRegex(ValueError,'above limit'):self.plan(fixtures(),location_mode='Area search',max_candidate_points=5)

if __name__=='__main__':unittest.main(verbosity=2)
