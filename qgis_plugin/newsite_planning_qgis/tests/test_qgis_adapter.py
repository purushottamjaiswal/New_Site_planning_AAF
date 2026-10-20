import ast,json,sys,tempfile,unittest,subprocess,configparser
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from shapely.geometry import Polygon,mapping
from newsite_planning_qgis.engine.constraints import PolygonConstraints
from newsite_planning_qgis.engine.planner import Rules,plan_frames
from test_planner import fixtures
from test_siting import candidates
ROOT=Path(__file__).resolve().parents[1]


def geo(geoms,ids=None):
    return {'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(g),
       'properties':{'opco':'NG','grid_id':(ids or ['a']*len(geoms))[i]}} for i,g in enumerate(geoms)]}

class AdapterTests(unittest.TestCase):
    def rules(self):return Rules(location_mode='Supplied candidates',min_airtel_km=.2,min_new_km=.05,max_sites=1)
    def raw(self):
        raw=fixtures();raw['candidates']=candidates([['NG','outside','a',0,.003,'Yes'],['NG','inside','a',0,.001,'Yes']]);return raw

    def test_actual_parent_boundary_rejects_nominally_inside_point(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'grid.geojson';path.write_text(json.dumps(geo([Polygon([(0,-.002),(.002,-.002),(.002,.002),(0,.002)])])))
            raw=self.raw();raw['_constraints']=PolygonConstraints(path,country='NG')
            p=plan_frames(raw,self.rules(),log=lambda s:None)
            self.assertEqual(p.sites.source_candidate_id.tolist(),['inside'])
            self.assertEqual(p.candidate_assessment.set_index('source_candidate_id').loc['outside','rejection'],'Outside actual parent grid polygon')

    def test_no_build_hole_is_permitted_but_boundary_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            outer=[(0,-.002),(.004,-.002),(.004,.002),(0,.002)];hole=[(.0005,-.0005),(.0015,-.0005),(.0015,.0005),(.0005,.0005)]
            path=Path(td)/'blocked.geojson';path.write_text(json.dumps(geo([Polygon(outer,[hole])])))
            raw=self.raw();raw['_constraints']=PolygonConstraints(no_build_path=path)
            p=plan_frames(raw,self.rules(),log=lambda s:None);self.assertEqual(p.sites.source_candidate_id.tolist(),['inside'])
            raw['candidates']=candidates([['NG','hole_edge','a',0,.0005,'Yes']])
            p=plan_frames(raw,self.rules(),log=lambda s:None);self.assertEqual(len(p.sites),0)

    def test_invalid_constraint_geometry_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'invalid.geojson';path.write_text(json.dumps(geo([Polygon([(0,0),(1,1),(1,0),(0,1)])])))
            with self.assertRaisesRegex(ValueError,'Invalid/non-polygon'):PolygonConstraints(path)

    def test_actual_area_controls_density_and_population_conversion(self):
        raw=fixtures();raw['grid']['area_km2_actual']=.25
        p=plan_frames(raw,Rules(location_mode='Grid centroid (legacy)',population_kind='People per km2',max_sites=1),log=lambda s:None)
        self.assertEqual(p.summary['target_demand_total'],275)
        self.assertEqual(p.grids.set_index('id').loc['a','density_per_km2'],100)
        raw['grid']['area_km2_actual']=0
        with self.assertRaisesRegex(ValueError,'Actual.*areas'):plan_frames(raw,Rules(),log=lambda s:None)

    def test_worker_protocol_and_spatial_polygon_export(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);raw=self.raw();paths={}
            for kind,frame in raw.items():
                target=base/(kind+'.csv');frame.to_csv(target,index=False);paths[kind]=str(target)
            boundary=base/'grid.geojson';boundary.write_text(json.dumps(geo([Polygon([(0,-.002),(.002,-.002),(.002,.002),(0,.002)])])));paths['grid_boundaries']=str(boundary)
            for role,geom in [('land_polygons',Polygon([(-179,-80),(179,-80),(179,80),(-179,80)])),('water_polygons',Polygon([(-160,-70),(-159,-70),(-159,-69),(-160,-69)]))]:
                p=base/(role+'.geojson');p.write_text(json.dumps(geo([geom])));paths[role]=str(p)
            cfg=base/'job.json';cfg.write_text(json.dumps({'paths':paths,'rules':vars(self.rules()),'output_dir':str(base/'out')}))
            run=subprocess.run([sys.executable,str(ROOT/'worker.py'),'--job',str(cfg)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            messages=[json.loads(line) for line in run.stdout.splitlines()];result=next(m for m in messages if m['event']=='result')
            spatial=json.loads(Path(result['layers']['grids']).read_text(encoding='utf-8'));self.assertEqual(len(spatial['features']),5)
            self.assertEqual(spatial['features'][0]['geometry']['type'],'Polygon')
            self.assertTrue((Path(result['folder'])/'qgis_layer_provenance.json').exists())

    def test_schema_and_rule_controls_stay_in_sync(self):
        schema=json.loads((ROOT/'schema.json').read_text(encoding='utf-8'));self.assertEqual(set(schema['defaults']),set(vars(Rules())))
        tree=ast.parse((ROOT/'dock.py').read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='make_rules')
        assign=next(n for n in method.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='groups' for t in n.targets))
        groups=ast.literal_eval(assign.value);self.assertEqual(set(k for _,keys in groups for k in keys),set(vars(Rules())))

    def test_plugin_metadata_and_lazy_entrypoint(self):
        cfg=configparser.ConfigParser();cfg.read(ROOT/'metadata.txt');self.assertEqual(cfg['general']['qgisMinimumVersion'],'3.34')
        import newsite_planning_qgis
        self.assertTrue(callable(newsite_planning_qgis.classFactory))
        tree=ast.parse((ROOT/'__init__.py').read_text(encoding='utf-8'));self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in tree.body))

if __name__=='__main__':unittest.main(verbosity=2)
