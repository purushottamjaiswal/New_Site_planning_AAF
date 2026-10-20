"""Quick demo skips alternative expansion, while retaining physical exclusions."""
import sys,tempfile,unittest
from pathlib import Path
from dataclasses import fields
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from planner import Rules,plan_frames
from test_planner import fixtures
import test_database_engine as database_tests
from mask_fixtures import masks
from shapely.geometry import box
from planner import run_paths

class QuickDemoTests(unittest.TestCase):
    def test_demo_bypasses_search_budget_but_detailed_mode_enforces_it(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);raw=fixtures()
            paths={**database_tests.DatabaseTests().files(root,raw),**masks(root)}
            rules=Rules(location_mode='Quick demo (grid centroid)',max_candidate_points=1,max_sites=20000)
            result=run_paths(paths,rules,log=lambda s:None)
            try:
                self.assertEqual(result.session.count('candidates'),result.summary['target_grids'])
                self.assertGreater(result.session.count('candidates'),1)
                self.assertTrue(result.sites.head(100).location_status.str.contains('Quick demo').all())
                self.assertTrue(any('QUICK DEMO' in n for n in result.summary['notes']))
                reference=plan_frames(raw,rules,log=lambda s:None)
                self.assertEqual(len(result.sites),len(reference.sites))
            finally:result.close()
            rules.location_mode='Area search'
            with self.assertRaisesRegex(ValueError,'Candidate alternatives exceed'):run_paths(paths,rules,log=lambda s:None)

    def test_demo_still_excludes_water_centroid_without_relocation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);paths={**database_tests.DatabaseTests().files(root,fixtures()),**masks(root,water=box(.007,-.001,.009,.001))}
            result=run_paths(paths,Rules(location_mode='Quick demo (grid centroid)',max_candidate_points=1),log=lambda s:None)
            try:
                rows=result.candidate_assessment.head(100).set_index('grid_id')
                self.assertIn('water',rows.loc['b','rejection'].lower())
                self.assertNotIn('b',result.sites.head(100).grid_id.tolist())
            finally:result.close()

    def test_ui_toggle_preserves_detailed_settings_and_handles_rooftop(self):
        import tkinter as tk
        from app import App
        master=tk.Tcl();defaults=Rules();values={}
        for field in fields(Rules):
            default=getattr(defaults,field.name)
            values[field.name]=tk.BooleanVar(master,value=default) if isinstance(default,bool) else tk.StringVar(master,value=str(default))
        values['location_mode'].set('Supplied candidates');values['search_spacing_m'].set('250');values['max_candidate_points'].set('100');values['building_rule'].set('Rooftop search')
        holder=SimpleNamespace(values=values,location_search_enabled=tk.BooleanVar(master,value=False))
        demo=App.get_rules(holder)
        self.assertEqual(demo.location_mode,'Quick demo (grid centroid)')
        self.assertEqual(demo.building_rule,'Context only')
        holder.location_search_enabled.set(True)
        detailed=App.get_rules(holder)
        self.assertEqual(detailed.location_mode,'Supplied candidates')
        self.assertEqual(detailed.max_candidate_points,100)
        self.assertEqual(detailed.building_rule,'Rooftop search')

if __name__=='__main__':unittest.main()
