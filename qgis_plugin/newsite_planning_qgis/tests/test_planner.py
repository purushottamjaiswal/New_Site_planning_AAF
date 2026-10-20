import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import json
import tempfile
import unittest
import numpy as np
import pandas as pd
from newsite_planning_qgis.engine.planner import Rules,plan_frames,save_plan,nearest,xyz,km_from_chord


def fixtures():
    grids=pd.DataFrame({
      'opco':['NG']*5+['KE'],'grid_id':['a','b','c','d','e','foreign'],
      'centroid_lat':['0']*6,'centroid_long':['0','.008','.03','.06','.07','0'],
      'combined_category_wf':['Red','Amber','Red','Green','Red','Red'],
      'coverage_category':['Red']*6,'throughput_category':['Amber']*6,
      'grid_size':['1km']*6,'month':["Sep'26"]*6,'total_mr_count':['100']*6,
      'site_present':['No']*6})
    airtel=pd.DataFrame({'opco_id':['NG','NG','KE'],'physical_site_id':['far','legacy','foreign'],
      'latitude':['0','0','0'],'longitude':['1','0','0'],'physical_site_status':['On-Air']*3,
      'technology':['4G','2G','4G'],'month':['Sep_2026']*3})
    comp=pd.DataFrame({'OPCO_ID':['NG','KE'],'SiteID':['comp','foreign'],
      'Latitude':['0']*2,'Longitude':['.008','0'],'Technology':['4G']*2,
      'OPCO_Comp':['MTN','Other'],'DB_Period':["May'26"]*2})
    pop=pd.DataFrame({'opco':['NG']*5+['KE'],'grid_id':['a','b','c','d','e','foreign'],
                      'population':['100','200','500','900','300','999999']})
    return {'grid':grids,'airtel':airtel,'competitor':comp,'population':pop}

class PlanningTests(unittest.TestCase):
    def plan(self,raw=None,**kw):
        settings={'location_mode':'Grid centroid (legacy)','distance_decay':0,'mode':'Population','service_radius_km':1.1,'min_airtel_km':.2,
                  'min_new_km':2,'max_sites':20};settings.update(kw)
        return plan_frames(raw or fixtures(),Rules(**settings),log=lambda s:None)

    def test_population_unique_assignment_and_spacing(self):
        p=self.plan();self.assertEqual(p.summary['target_demand_total'],1100)
        self.assertAlmostEqual(p.sites.incremental_demand.sum(),p.grids.loc[p.grids.assigned_candidate.ne(''),'demand'].sum())
        self.assertLessEqual(p.sites.incremental_demand.sum(),1100)
        self.assertNotIn('foreign',p.grids.id.tolist())
        for i in range(len(p.sites)):
            for j in range(i):
                distance=km_from_chord(np.linalg.norm(xyz(p.sites.iloc[[i]])-xyz(p.sites.iloc[[j]])))
                self.assertGreaterEqual(float(distance),2-1e-8)
        self.assertTrue((p.sites.nearest_airtel_km>=.2).all())

    def test_population_density_3km_area(self):
        raw=fixtures();raw['grid']['grid_size']='3km'
        p=self.plan(raw,grid_size='3km',population_kind='People per km2')
        self.assertEqual(p.summary['target_demand_total'],1100*9)
        self.assertEqual(p.grids.loc[p.grids.id=='a','density_per_km2'].iloc[0],100)

    def test_unknown_population_is_not_zero_or_mr(self):
        raw=fixtures();raw['population']=raw['population'].query("grid_id != 'a'")
        p=self.plan(raw);self.assertEqual(p.summary['population_missing_poor_grids'],1)
        row=p.grids.set_index('id').loc['a'];self.assertTrue(np.isnan(row.demand));self.assertEqual(row.assigned_candidate,'')
        del raw['population']
        with self.assertRaisesRegex(ValueError,'Population is absent'):self.plan(raw)

    def test_near_site_and_planned_reservation(self):
        raw=fixtures();raw['airtel'].loc[0,'longitude']='0'
        p=self.plan(raw);self.assertNotIn('a',p.sites.grid_id.tolist())
        raw['airtel'].loc[1,['longitude','technology','physical_site_status']]=['.03','4G','Planned - NFA Approved']
        p=self.plan(raw,reserve_planned=True);self.assertNotIn('c',p.sites.grid_id.tolist())
        p=self.plan(raw,reserve_planned=False);self.assertIn('c',p.sites.grid_id.tolist())

    def test_duplicate_population_refused(self):
        raw=fixtures();raw['population']=pd.concat([raw['population'],raw['population'].iloc[[0]]])
        with self.assertRaisesRegex(ValueError,'duplicate keys'):self.plan(raw)

    def test_zero_candidates_exports_headers(self):
        p=self.plan(min_airtel_km=1000)
        self.assertEqual(len(p.sites),0)
        with tempfile.TemporaryDirectory() as td:
            folder=save_plan(p,td)
            self.assertIn('candidate_id',pd.read_csv(folder/'proposed_sites.csv').columns)
            self.assertEqual(json.loads((folder/'proposed_sites.geojson').read_text())['features'],[])
            self.assertIn('const D=',(folder/'planning_map.html').read_text())

    def test_invalid_coordinates_are_audited(self):
        raw=fixtures();raw['grid'].loc[0,'centroid_lat']='200'
        p=self.plan(raw);self.assertEqual(p.summary['invalid_coordinate_rows'],1)
        self.assertNotIn('a',p.grids.id.tolist())

    def test_competitor_boost_and_country_isolation(self):
        p=self.plan(competitor_radius_km=1,competitor_boost=.5)
        g=p.grids.set_index('id');self.assertEqual(g.loc['a','weighted_demand'],150)
        self.assertEqual(g.loc['b','weighted_demand'],180)
        self.assertTrue((p.competitors.country=='NG').all())

    def test_min_incremental_and_density_filter(self):
        p=self.plan(min_incremental=501,service_radius_km=.1)
        self.assertEqual(len(p.sites),0)
        p=self.plan(min_density=201)
        self.assertEqual(p.summary['target_demand_total'],800)

    def test_mr_preview_not_population(self):
        raw=fixtures();del raw['population']
        p=self.plan(raw,mode='MR preview')
        self.assertIn('NOT population',p.summary['demand_unit']);self.assertTrue(p.grids.population.isna().all())

    def test_spherical_distance_and_no_antimeridian_bug(self):
        a=pd.DataFrame({'lat':[0],'lon':[179.99]});b=pd.DataFrame({'lat':[0],'lon':[-179.99]})
        d,_=nearest(a,b);self.assertAlmostEqual(d[0],2.2239,places=3)

    def test_ambiguous_competitor_is_quarantined(self):
        raw=fixtures();extra=raw['competitor'].iloc[[0]].copy();extra['Longitude']='.05'
        raw['competitor']=pd.concat([raw['competitor'],extra])
        p=self.plan(raw);self.assertEqual(len(p.competitors),0)
        self.assertEqual(len(p.issues),2)
        self.assertTrue(p.grids.nearest_competitor_km.isna().all())

    def test_bad_rules_and_cancellation(self):
        with self.assertRaises(ValueError):self.plan(service_radius_km=float('nan'))
        with self.assertRaises(InterruptedError):plan_frames(fixtures(),Rules(),log=lambda s:None,cancelled=lambda:True)

if __name__=='__main__':unittest.main(verbosity=2)
