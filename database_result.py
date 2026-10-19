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
                def write(row):writer.writerow({rename.get(k,k):v for k,v in row.items() if k in columns})
                if first is not None:write(first)
                for index,row in enumerate(iterator,1):
                    if index%2000==0:s.check()
                    write(row)
        def schema(table,defaults=()):
            keys=dict.fromkeys(defaults)
            if table!='issues':
                for key, in s.db.execute(f'SELECT DISTINCT j.key FROM {table}, json_each({table}.payload) j'):keys.setdefault(key,None)
            return list(keys)
        csv_export('proposed_sites.csv',rows('sites'),SITE_COLUMNS)
        csv_export('grid_assessment.csv',rows('grids'),schema('grids'),{'id':'grid_id','size':'grid_size','country':'opco'})
        csv_export('candidate_location_assessment.csv',rows('candidates'),schema('candidates'))
        csv_export('demand_point_assessment.csv',rows('demand'),schema('demand'))
        csv_export('demand_point_assignments.csv',rows('demand',"assigned<>''"),schema('demand'))
        def assignments():
            query="SELECT assigned,gid,SUM(json_extract(payload,'$.demand')),COUNT(*) FROM demand WHERE assigned<>'' GROUP BY assigned,gid ORDER BY assigned,gid"
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
        if s.count('no_build'):geojson('no_build_polygons_used.geojson','no_build',True)
        if self.buildings_context:self.buildings_context.export_geojson(folder/'building_footprints_used.geojson')
        metadata={'rules':asdict(self.rules),'summary':self.summary,'sources':{key:source_fingerprint(path) for key,path in (paths or {}).items() if path},'source_layers_and_crs':self.input_metadata,'temporary_database':str(s.path),'temporary_database_lifecycle':'Removed when this result/session is closed. Full output tables are already exported. Map viewport endpoints require the active session.'}
        (folder/'run_settings.json').write_text(json.dumps(clean(metadata),indent=2,allow_nan=False),encoding='utf-8')
        # Only bounded frames reach the legacy HTML renderers.
        from shapely.geometry import shape
        grids=s.preview('grids',5000);sites=s.preview('sites',500);airtel=s.preview('airtel',5000);comp=s.preview('competitors',5000)
        for frame,cols in ((grids,['id','lat','lon','category','town','assigned_candidate']),(sites,SITE_COLUMNS),(airtel,['id','lat','lon','planning_role','technology','status']),(comp,['id','lat','lon','operator','period','technology'])):
            for col in cols:
                if col not in frame:frame[col]=pd.Series(dtype=object)
        polygon_grids={row['id']:shape(json.loads(geom)) for _,row,geom in s.records('grids',limit=5000) if geom}
        exclusions=[shape(json.loads(geom)) for _,_,geom in s.records('no_build',limit=5000)]
        preview_summary={**self.summary,'notes':self.summary['notes']+['Static map previews: up to 500 planned sites, 5,000 grids and 5,000 records per inventory. Full CSV/GeoJSON exports include every result; live OpenLayers viewport queries retrieve local records.']}
        preview=SimpleNamespace(sites=sites,grids=grids,airtel=airtel,competitors=comp,demand_points=pd.DataFrame(columns=['basis','point_id','grid_id','lat','lon','demand','assigned_candidate']),summary=preview_summary,rules=self.rules,buildings_context=self.buildings_context,polygon_context=SimpleNamespace(grids=polygon_grids,exclusions=exclusions))
        from map_report import write_map
        from web_maps import write_web_map
        from openlayers_map import write_openlayers_map
        write_map(preview,folder/'planning_map.html');write_web_map(preview,folder/'planning_web_map.html');write_openlayers_map(preview,folder/'planning_openlayers_map.html')
        self.export_earth(folder/'planning_google_earth.kml')
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
