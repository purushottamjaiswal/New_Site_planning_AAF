"""Paged session results and streaming full exports; browser previews remain bounded."""
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from itertools import islice
import csv,json,math,zipfile
import pandas as pd
from session_db import DiskTable,clean,encode

class DatabaseResult:
    def __init__(self,session,rules,summary):
        self.session=session;self.rules=rules;self.summary=summary;self.input_metadata=session.metadata;self.buildings_context=session.buildings;self.polygon_context=None
        for key,table in [('sites','sites'),('grids','grids'),('airtel','airtel'),('competitors','competitors'),('issues','issues'),('candidate_assessment','candidates'),('demand_points','demand')]:setattr(self,key,DiskTable(session,table))
    def close(self):self.session.close()
    def site_page(self,offset=0,limit=200):return self.session.preview('sites',min(limit,500),offset)
    def export(self,root,paths=None):
        from planner import SITE_COLUMNS,source_fingerprint
        s=self.session;folder=Path(root)/('plan_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'));folder.mkdir(parents=True,exist_ok=False)
        def rows(table,where='1',params=()):
            if table=='issues':
                for payload, in s.db.execute('SELECT payload FROM issues ORDER BY rid'):yield json.loads(payload)
            else:
                for _,row,_ in s.records(table,where,params):yield row
        def csv_export(name,records,columns=None,rename=None):
            # Determine union of schema keys on disk, rather than collecting records.
            rename=rename or {};iterator=iter(records);first=next(iterator,None)
            if columns is None:columns=list(first) if first else []
            with (folder/name).open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=[rename.get(c,c) for c in columns],extrasaction='ignore');writer.writeheader()
                def write(row):writer.writerow({rename.get(k,k):json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in row.items() if k in columns})
                if first is not None:write(first)
                for index,row in enumerate(iterator,1):
                    if index%2000==0:s.check()
                    write(row)
        def schema(table,defaults=()):
            keys=dict.fromkeys(defaults)
            if table!='issues':
                for key, in s.db.execute(f'SELECT DISTINCT j.key FROM {table}, json_each({table}.payload) j'):keys.setdefault(key,None)
            return list(keys)
        csv_export('alternative_candidates.csv',(json.loads(x[0]) for x in s.db.execute('SELECT payload FROM alternatives')),['selected_candidate','comparison_basis','source_candidate_id','grid_id','lat','lon','weighted_benefit','incremental_demand','red_demand','amber_demand'])
        csv_export('proposed_sites.csv',rows('sites'),SITE_COLUMNS)
        csv_export('grid_assessment.csv',rows('grids'),schema('grids'),{'id':'grid_id','size':'grid_size','country':'opco'})
        csv_export('candidate_location_assessment.csv',rows('candidates'),schema('candidates'))
        csv_export('demand_point_assessment.csv',rows('demand'),schema('demand'))
        csv_export('demand_point_assignments.csv',rows('demand',"assigned<>''"),schema('demand'))
        def assignments():
            query="SELECT assigned,json_extract(payload,'$.grid_id'),SUM(json_extract(payload,'$.demand')),COUNT(*) FROM demand WHERE assigned<>'' GROUP BY assigned,json_extract(payload,'$.grid_id') ORDER BY assigned,json_extract(payload,'$.grid_id')"
            for cid,gid,total,count in s.db.execute(query):yield dict(assigned_candidate=cid,grid_id=gid,assigned_demand=total,assigned_points=count)
        csv_export('site_grid_assignments.csv',assignments(),['assigned_candidate','grid_id','assigned_demand','assigned_points'])
        csv_export('data_quality_issues.csv',rows('issues'),['source','source_row','id','reason'])
        csv_export('optimization_review.csv',rows('grids',"json_extract(payload,'$.candidate_exclusion') LIKE '%assess optimization%'"),schema('grids'))
        for table,name in [('airtel','airtel_sites_used.csv'),('competitors','competitor_sites_used.csv')]:csv_export(name,rows(table),schema(table))
        def geojson(name,table,polygon=False):
            with (folder/name).open('w',encoding='utf-8') as f:
                f.write('{"type":"FeatureCollection","features":[');first=True
                for rid,row,geom in s.records(table):
                    if polygon and not geom:continue
                    feature={'type':'Feature','geometry':json.loads(geom) if polygon else {'type':'Point','coordinates':[row['lon'],row['lat']]},'properties':row}
                    if not first:f.write(',')
                    f.write(encode(feature));first=False
                f.write(']}')
        geojson('proposed_sites.geojson','sites');geojson('grid_assessment_spatial.geojson','grids',True)
        geojson('land_polygons_used.geojson','land',True);geojson('water_polygons_used.geojson','water',True)
        if s.count('no_build'):geojson('no_build_polygons_used.geojson','no_build',True)
        if self.buildings_context:self.buildings_context.export_geojson(folder/'building_footprints_used.geojson')
        self.summary['grid_bounds']=s.db.execute('SELECT min(minx),min(miny),max(maxx),max(maxy) FROM grids_rt').fetchone()
        if self.rules.export_gpkg:
            from gpkg_export import export_results
            export_results(s,folder/'planning_results.gpkg')
        project_path=None
        if self.rules.persist_project:
            from project_review import save_project
            project_path=save_project(s,folder,self.rules,paths or {})
            self.summary['project_database']=str(project_path)
        if project_path and self.buildings_context:
            self.input_metadata={**self.input_metadata,'buildings':{**self.input_metadata.get('buildings',{}),'index_path':str(folder/'building_index.sqlite')}}
        import importlib.metadata,platform,sys
        versions={name:importlib.metadata.version(name) for name in ('numpy','pandas','scipy','shapely','pyproj','pyogrio','ijson','rasterio')}
        metadata={'software_versions':{'python':sys.version,'platform':platform.platform(),**versions},'engine_version':'7.0','result_database':str(project_path) if project_path else None,'rules':asdict(self.rules),'summary':self.summary,'sources':{key:source_fingerprint(path) for key,path in (paths or {}).items() if path},'source_layers_and_crs':self.input_metadata,'temporary_database':str(s.path),'temporary_database_lifecycle':'Removed when this result/session is closed. Full output tables are already exported. Map viewport endpoints require the active session.'}
        (folder/'run_settings.json').write_text(json.dumps(clean(metadata),indent=2,allow_nan=False),encoding='utf-8')
        # Only bounded frames reach the legacy HTML renderers.
        from shapely.geometry import shape
        grids=s.preview('grids',5000);sites=s.preview('sites',500);airtel=s.preview('airtel',5000);comp=s.preview('competitors',5000)
        for frame,cols in ((grids,['id','lat','lon','category','town','assigned_candidate']),(sites,SITE_COLUMNS),(airtel,['id','lat','lon','planning_role','technology','status']),(comp,['id','lat','lon','operator','period','technology'])):
            for col in cols:
                if col not in frame:frame[col]=pd.Series(dtype=object)
        polygon_grids={row['id']:shape(json.loads(geom)) for _,row,geom in s.records('grids',limit=5000) if geom}
        exclusions=[shape(json.loads(geom)) for _,_,geom in s.records('no_build',limit=5000)]
        preview_summary={**self.summary,'database_path':str(project_path) if project_path else str(s.path),'notes':self.summary['notes']+['Initial map previews: up to 500 planned sites, 5,000 grids and 5,000 records per inventory. Full CSV/GeoJSON exports include every result; live offline/OpenLayers viewport queries retrieve local records.']}
        preview=SimpleNamespace(sites=sites,grids=grids,airtel=airtel,competitors=comp,demand_points=pd.DataFrame(columns=['basis','point_id','grid_id','lat','lon','demand','assigned_candidate']),summary=preview_summary,rules=self.rules,buildings_context=self.buildings_context,polygon_context=SimpleNamespace(grids=polygon_grids,exclusions=exclusions))
        from map_report import write_map
        from web_maps import write_web_map
        from openlayers_map import write_openlayers_map
        write_map(preview,folder/'planning_map.html');write_web_map(preview,folder/'planning_web_map.html');write_openlayers_map(preview,folder/'planning_openlayers_map.html')
        self.export_earth(folder/'planning_google_earth.kml')
        runner=Path(__file__).with_name('review_project.py').resolve()
        (folder/'Open_Result_Map.bat').write_text('@echo off\n"'+str(Path(__import__('sys').executable))+'" "'+str(runner)+'" --folder "'+str(folder.resolve())+'"\npause\n',encoding='utf-8')
        (folder/'EXPORT_COMPLETE.txt').write_text('Export completed. Original plan is immutable; draft reviews are stored separately.\n')
        s.log('Full tables exported in batches; desktop and static-map previews are paginated/capped.')
        return folder
    def export_earth(self,path):
        from xml.sax.saxutils import escape
        path=Path(path)
        with path.open('w',encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="utf-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Site planning review</name>')
            for table,name in [('sites','All proposed sites'),('airtel','Airtel inventory'),('competitors','Competitor inventory')]:
                f.write('<Folder><name>'+name+'</name>')
                for _,row,_ in self.session.records(table):
                    label=escape(str(row.get('candidate_id',row.get('id','Site'))));f.write(f'<Placemark><name>{label}</name><Point><altitudeMode>clampToGround</altitudeMode><coordinates>{row["lon"]},{row["lat"]},0</coordinates></Point></Placemark>')
                f.write('</Folder>')
            if self.buildings_context:
                f.write('<Folder><name>Building footprint preview (up to 5,000; full GeoJSON supplied)</name>')
                for feature in islice(iter(self.buildings_context.features),5000):
                    props=feature['properties'];geom=feature['geometry'];polygons=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates']
                    f.write('<Placemark><name>'+escape(str(props.get('building_id','Building')))+'</name><description>'+escape(str(props.get('source',''))+'; '+str(props.get('license','Verify original license')))+'</description><MultiGeometry>')
                    for coords in polygons:
                        f.write('<Polygon><altitudeMode>clampToGround</altitudeMode>')
                        for i,ring in enumerate(coords):
                            tag='outerBoundaryIs' if i==0 else 'innerBoundaryIs';f.write('<'+tag+'><LinearRing><coordinates>'+' '.join(str(c[0])+','+str(c[1])+',0' for c in ring)+'</coordinates></LinearRing></'+tag+'>')
                        f.write('</Polygon>')
                    f.write('</MultiGeometry></Placemark>')
                f.write('</Folder>')
            f.write('</Document></kml>')
        with zipfile.ZipFile(path.with_suffix('.kmz'),'w',zipfile.ZIP_DEFLATED) as z:z.write(path,'doc.kml')
