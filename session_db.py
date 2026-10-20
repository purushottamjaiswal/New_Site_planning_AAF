"""Temporary SQLite session with bounded import/export and spherical spatial lookups."""
from pathlib import Path
from contextlib import closing
import csv,json,math,sqlite3,tempfile,shutil,atexit
import numpy as np
import pandas as pd
from stream_io import stream_source

EARTH=6371.0088

def number(value):
    try:return float(str(value).replace(',','').strip())
    except (ValueError,TypeError):return float('nan')
def valid(lat,lon):return math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180
def distance(lat,lon,y,x):
    a,b,c,d=map(math.radians,(lat,lon,y,x));h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 2*EARTH*math.asin(math.sqrt(max(0,min(1,h))))
def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,(float,np.floating)):return float(value) if math.isfinite(value) else None
    if isinstance(value,np.integer):return int(value)
    return value
def encode(row):return json.dumps(clean(row),allow_nan=False,separators=(',',':'))

class Session:
    def __init__(self,log=lambda s:None,cancelled=lambda:False,batch_size=2000):
        self.folder=Path(tempfile.mkdtemp(prefix='aaf_site_session_'));self.path=self.folder/'planning.sqlite';self.log=log;self.cancelled=cancelled;self.batch_size=batch_size;self.closed=False
        self.db=sqlite3.connect(str(self.path),check_same_thread=False)
        self.db.execute('PRAGMA cache_size=-16384');self.db.execute('PRAGMA temp_store=FILE');self.db.execute('PRAGMA mmap_size=0')
        self.db.executescript('''CREATE TABLE inputs(rid INTEGER PRIMARY KEY,role TEXT,country TEXT,gid TEXT,size TEXT,period TEXT,payload TEXT,raw_data TEXT,geometry TEXT);
        CREATE INDEX input_scope ON inputs(role,country,period,size,gid); CREATE INDEX input_ids ON inputs(role,country,gid);
        CREATE TABLE issues(rid INTEGER PRIMARY KEY,payload TEXT);''')
        for table in ('grids','airtel','competitors','infrastructure','buffers','no_build','land','water','demand','candidates','sites','network','rf_coverage'):
            self.db.execute(f'CREATE TABLE {table}(rid INTEGER PRIMARY KEY,gid TEXT,lat REAL,lon REAL,payload TEXT,geometry TEXT)')
            self.db.execute(f'CREATE INDEX {table}_gid ON {table}(gid)')
            if table in ('grids','sites','airtel','competitors'):self.db.execute(f'CREATE INDEX {table}_search_id ON {table}(gid COLLATE NOCASE)')
            self.db.execute(f'CREATE VIRTUAL TABLE {table}_rt USING rtree(rid,minx,maxx,miny,maxy)')
        self.db.executescript('''ALTER TABLE demand ADD COLUMN assigned TEXT DEFAULT ''; ALTER TABLE demand ADD COLUMN distance REAL;
        ALTER TABLE candidates ADD COLUMN allowed INTEGER DEFAULT 1; ALTER TABLE candidates ADD COLUMN score REAL DEFAULT 0; ALTER TABLE candidates ADD COLUMN stamp INTEGER DEFAULT -1; ALTER TABLE candidates ADD COLUMN red_score REAL DEFAULT 0;
        CREATE INDEX candidate_priority ON candidates(allowed,score DESC,rid); CREATE INDEX candidate_red_priority ON candidates(allowed,red_score DESC,score DESC,rid); CREATE TABLE alternatives(payload TEXT); CREATE INDEX alternatives_site ON alternatives(json_extract(payload,'$.selected_candidate')); CREATE INDEX demand_assigned ON demand(assigned,gid); CREATE INDEX demand_parent ON demand(json_extract(payload,'$.grid_id')); CREATE INDEX candidate_parent ON candidates(json_extract(payload,'$.grid_id'),allowed,score DESC);
        CREATE TABLE selected_stats(gid TEXT PRIMARY KEY,total REAL,weighted_distance REAL,count_sites INTEGER,ids TEXT);''')
        self.metadata={};self.buildings=None;atexit.register(self.close)
    def check(self):
        if self.cancelled():raise InterruptedError('Planning cancelled; temporary session will be removed.')
    def close(self):
        if not self.closed:

            if getattr(self,'engineering',None):self.engineering.close()
            self.db.close();shutil.rmtree(self.folder,ignore_errors=True);self.closed=True;atexit.unregister(self.close)
    def issue(self,role,row,reason):self.db.execute('INSERT INTO issues(payload) VALUES(?)',(encode({'source':role,'source_row':row.get('source_row'),'id':row.get('id',''),'reason':reason}),))
    def add(self,table,row,geometry=None):
        gid=str(row.get('id',row.get('candidate_id',row.get('point_id',row.get('source_candidate_id','')))))
        lat=row.get('lat');lon=row.get('lon')
        cur=self.db.execute(f'INSERT INTO {table}(gid,lat,lon,payload,geometry) VALUES(?,?,?,?,?)',(gid,lat,lon,encode(row),geometry))
        rid=cur.lastrowid
        if lat is not None and lon is not None and valid(lat,lon):self.db.execute(f'INSERT INTO {table}_rt VALUES(?,?,?,?,?)',(rid,lon,lon,lat,lat))
        if table=='grids' and geometry:
            from shapely.geometry import shape
            w,south,e,n=shape(json.loads(geometry)).bounds
            self.db.execute('UPDATE grids_rt SET minx=?,maxx=?,miny=?,maxy=? WHERE rid=?',(w,e,south,n,rid))
        return rid
    def update(self,table,rid,row):self.db.execute(f'UPDATE {table} SET payload=? WHERE rid=?',(encode(row),rid))
    def count(self,table,where='1',params=()):return self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}',params).fetchone()[0]
    def records(self,table,where='1',params=(),order='rid',limit=None,offset=0):
        query=f'SELECT rid,payload,geometry FROM {table} WHERE {where} ORDER BY {order}'
        args=list(params)
        if limit is not None:query+=' LIMIT ? OFFSET ?';args.extend([limit,offset])
        if table=='issues':query=query.replace('rid,payload,geometry','rid,payload,NULL')
        cursor=self.db.execute(query,args)
        while True:
            batch=cursor.fetchmany(self.batch_size)
            if not batch:break
            self.check()
            for rid,payload,geometry in batch:yield rid,json.loads(payload),geometry
    def input_rows(self,role,**scope):
        where=['role=?'];params=[role]
        for col,value in scope.items():where.append(col+'=?');params.append(value)
        cursor=self.db.execute('SELECT rid,payload,geometry FROM inputs WHERE '+' AND '.join(where)+' ORDER BY gid,rid',params)
        while True:
            batch=cursor.fetchmany(self.batch_size)
            if not batch:break
            self.check()
            for rid,payload,geom in batch:yield rid,json.loads(payload),geom
    def unique(self,role,**scope):
        where=['role=?'];params=[role]
        for col,val in scope.items():where.append(col+'=?');params.append(val)
        base=' FROM inputs WHERE '+' AND '.join(where)
        if self.db.execute('SELECT 1'+base+" AND (gid='' OR country='') LIMIT 1",params).fetchone():raise ValueError(f'{role}: blank identifiers. Correct them before planning.')
        if self.db.execute('SELECT gid'+base+' GROUP BY country,gid HAVING COUNT(*)>1 LIMIT 1',params).fetchone():raise ValueError(f'{role}: duplicate keys. Resolve duplicate records first.')
    def ingest(self,paths,rules,mappings=None,source_options=None):
        from planner import ALIASES,canonical
        from gis_io import match_mapping,normalize_role
        from shapely.geometry import mapping as geom_mapping
        mappings=mappings or {};options=source_options or {}
        for role,path in paths.items():
            if not path or role in ('buildings','terrain','grid_boundaries'):continue
            self.check();self.log(f'Importing {role} into temporary database: {Path(path).name}')
            opt=options.get(role,{});mapping=None;count=0;stored=0
            for data in stream_source(path,layer=opt.get('layer'),crs_override=opt.get('crs_override'),batch_size=self.batch_size,cancelled=self.cancelled):
                if role in ('road_lines','backhaul_lines','power_lines','rf_coverage'):
                    if data.geometries is None:raise ValueError(role+': geometry and CRS are required.')
                    for i,geom in enumerate(data.geometries):
                        if geom is None or geom.is_empty or not geom.is_valid:raise ValueError(role+': invalid geometry.')
                        props=data.frame.iloc[i].to_dict();table='rf_coverage' if role=='rf_coverage' else 'network'
                        if role=='rf_coverage':
                            if geom.geom_type not in ('Polygon','MultiPolygon'):raise ValueError('RF evidence must be coverage polygons.')
                            if not all(str(props.get(k,'')).strip() for k in ('candidate_id','calibration_id','validation_date')) or str(props.get('validated','')).lower() not in ('yes','true','1'):raise ValueError('RF footprints require candidate_id, calibration_id, validation_date and validated=Yes. Supply externally validated footprints, not assumed circles.')
                            from datetime import date
                            try:date.fromisoformat(str(props['validation_date']))
                            except ValueError:raise ValueError('RF validation_date must be YYYY-MM-DD.')
                            props['id']=str(props['candidate_id']).strip()
                        else:
                            if geom.geom_type not in ('LineString','MultiLineString'):raise ValueError(role+': use line or multiline geometry.')
                            props.update(id=str(count),kind=role.split('_')[0])
                        props.update(lat=geom.centroid.y,lon=geom.centroid.x);rid=self.add(table,props,json.dumps(geom_mapping(geom)));w,south,e,n=geom.bounds
                        self.db.execute(f'UPDATE {table}_rt SET minx=?,maxx=?,miny=?,maxy=? WHERE rid=?',(w,e,south,n,rid));count+=1
                elif role in ('no_build_polygons','land_polygons','water_polygons'):
                    if data.geometries is None:raise ValueError(f'{role}: select a polygon layer with known CRS.')
                    for geom_index,geom in enumerate(data.geometries):
                        if geom is None or geom.geom_type not in ('Polygon','MultiPolygon') or not geom.is_valid or geom.is_empty:raise ValueError(f'{role}: invalid or non-polygon geometry; repair the source. River centre lines require an actual width-based polygon.')
                        w,south,e,n=geom.bounds
                        if not(-180<=w<=e<=180 and -90<=south<=n<=90):raise ValueError(f'{role}: polygon coordinates are not WGS84.')
                        properties=data.frame.iloc[geom_index].to_dict()
                        if role=='no_build_polygons':self.db.execute('INSERT INTO inputs(role,country,gid,size,period,payload,raw_data,geometry) VALUES(?,?,?,?,?,?,?,?)',('no_build_polygons','',str(count),'','',encode(properties),encode(properties),json.dumps(geom_mapping(geom))))
                        table={'no_build_polygons':'no_build','land_polygons':'land','water_polygons':'water'}[role]
                        row={**properties,'id':str(count),'lat':geom.centroid.y,'lon':geom.centroid.x};rid=self.add(table,row,json.dumps(geom_mapping(geom)));w,s,e,n=geom.bounds
                        self.db.execute(f'UPDATE {table}_rt SET minx=?,maxx=?,miny=?,maxy=? WHERE rid=?',(w,e,s,n,rid));count+=1
                else:
                    if role not in ALIASES:raise ValueError('Unknown data source role: '+role)
                    if mapping is None:mapping,_=match_mapping(data.frame,role,mappings.get(role,ALIASES[role]),ALIASES[role])
                    frame,effective,polygons=normalize_role(data,role,mapping,opt);canon=canonical(frame,role,effective);canon['source_row']=np.arange(len(canon))+count+2
                    keep=canon.country.eq(rules.country.upper()) if 'country' in canon else pd.Series(True,index=canon.index)
                    if role=='grid':keep &= canon['size'].str.lower().str.replace(' ','').eq(rules.grid_size)&canon.period.eq(rules.grid_period)
                    if role=='airtel':keep &= canon.period.eq(rules.airtel_period)
                    raw=data.frame.to_dict('records')
                    for i,row in enumerate(canon.to_dict('records')):
                        if not keep.iloc[i]:continue
                        if role=='grid':row['size']=str(row['size']).lower().replace(' ','')
                        geom=None
                        if role=='grid' and data.geometries is not None:
                            geometry=data.geometries[i]
                            if geometry is not None and geometry.geom_type in ('Polygon','MultiPolygon'):geom=json.dumps(geom_mapping(geometry))
                        self.db.execute('INSERT INTO inputs(role,country,gid,size,period,payload,raw_data,geometry) VALUES(?,?,?,?,?,?,?,?)',
                            (role,row.get('country',''),row.get('id',''),row.get('size',''),row.get('period',''),encode(row),encode(raw[i]),geom))
                    count+=len(canon)
                self.metadata[role]={**data.metadata,'mapping':effective if role in ALIASES else {},'records':self.count('inputs',"role=?",(role,)) if role in ALIASES else count,'source_records_scanned':count,'storage':'temporary SQLite'}
                self.db.commit()
                if count%20000<self.batch_size:self.log(f'{role}: {count:,} source records scanned; selected-scope rows staged')
            if not count and role in ('grid','airtel','candidates','land_polygons','water_polygons'):raise ValueError(f'{role}: source is empty.')
        if paths.get('grid_boundaries'):
            for data in stream_source(paths['grid_boundaries'],batch_size=self.batch_size,cancelled=self.cancelled):
                if data.geometries is None:raise ValueError('Grid boundaries need polygon geometry.')
                for i,geom in enumerate(data.geometries):
                    props=data.frame.iloc[i].to_dict();gid=str(props.get('grid_id',''));country=str(props.get('opco',rules.country)).upper()
                    if country!=rules.country.upper():continue
                    if not gid or geom is None or geom.geom_type not in ('Polygon','MultiPolygon') or not geom.is_valid:raise ValueError('Invalid supplemental grid boundary.')
                    self.db.execute("UPDATE inputs SET geometry=? WHERE role='grid' AND country=? AND gid=?",(json.dumps(geom_mapping(geom)),country,gid))
            self.db.commit()
        if paths.get('buildings'):
            from buildings import load_buildings
            from dataclasses import replace
            bounds=self.db.execute("SELECT min(CAST(json_extract(payload,'$.lon') AS REAL)),min(CAST(json_extract(payload,'$.lat') AS REAL)),max(CAST(json_extract(payload,'$.lon') AS REAL)),max(CAST(json_extract(payload,'$.lat') AS REAL)) FROM inputs WHERE role='grid'").fetchone()
            building_rules=rules
            if bounds[0] is not None:
                w,south,e,n=bounds;margin=(rules.service_radius_km+5)/110;dx=margin/max(.01,math.cos(math.radians(max(abs(south),abs(n)))))
                clip=[max(-90,south-margin),max(-180,w-dx),min(90,n+margin),min(180,e+dx)]
                if rules.building_clip_bbox:
                    a,b,c,d=map(float,rules.building_clip_bbox.split(','));clip=[max(a,clip[0]),max(b,clip[1]),min(c,clip[2]),min(d,clip[3])]
                building_rules=replace(rules,building_clip_bbox=','.join(map(str,clip)))
            opt=options.get('buildings',{});self.buildings=load_buildings(paths['buildings'],building_rules,self.cancelled,layer=opt.get('layer'),crs_override=opt.get('crs_override'),disk=True,cache_dir=self.folder/'building_index',log=self.log)
            self.metadata['buildings']={**self.buildings.metadata,'storage':'temporary SQLite RTree'}
    def spatial(self,table,lat,lon,radius,where='1',params=()):
        # Exact spherical cap bounds, including the date line and poles.
        angle=min(math.pi,radius/EARTH);dy=math.degrees(angle);s=max(-90,lat-dy);n=min(90,lat+dy)
        dx=180 if s<=-90 or n>=90 or angle>=math.pi/2 else math.degrees(math.asin(min(1,math.sin(angle)/max(1e-15,math.cos(math.radians(lat))))))
        w=lon-dx;e=lon+dx
        boxes=[(-180,180)] if dx>=180 else [(w+360,180),(-180,e)] if w< -180 else [(w,180),(-180,e-360)] if e>180 else [(w,e)]
        for west,east in boxes:
            cursor=self.db.execute(f'SELECT t.rid,t.payload,t.lat,t.lon,t.geometry FROM {table}_rt b CROSS JOIN {table} t WHERE t.rid=b.rid AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND ({where})',(west,east,s,n,*params))
            while True:
                batch=cursor.fetchmany(self.batch_size)
                if not batch:break
                self.check()
                for rid,payload,y,x,geom in batch:
                    dist=distance(lat,lon,y,x)
                    if dist<=radius+1e-10:yield rid,json.loads(payload),dist,geom
    def nearest(self,table,lat,lon,where='1',params=()):
        if not self.db.execute(f'SELECT 1 FROM {table} t WHERE '+where+' LIMIT 1',params).fetchone():return float('nan'),{}
        radius=.5
        while True:
            best=None
            for rid,row,dist,_ in self.spatial(table,lat,lon,min(radius,math.pi*EARTH),where,params):
                if best is None or (dist,rid)<(best[0],best[1]):best=(dist,rid,row)
            if best is not None:return best[0],best[2]
            if radius>=math.pi*EARTH:return float('nan'),{}
            radius=min(radius*2,math.pi*EARTH)
    def preview(self,table,limit=500,offset=0):return pd.DataFrame([r for _,r,_ in self.records(table,limit=limit,offset=offset)])

class DiskColumn:
    def __init__(self,table,name):self.table=table;self.name=name
    @property
    def iloc(self):return self
    def __getitem__(self,index):return next(self.table.session.records(self.table.name,limit=1,offset=int(index)))[1].get(self.name)
    def tolist(self):return [row.get(self.name) for _,row,_ in self.table.session.records(self.table.name)]
class DiskTable:
    def __init__(self,session,name):self.session=session;self.name=name
    def __len__(self):return self.session.count(self.name)
    def head(self,n=500):return self.session.preview(self.name,n)
    def __getattr__(self,name):return DiskColumn(self,name)
    def to_dict(self,orient='records'):
        raise ValueError('Database results are paginated. Use head(n).to_dict or site_page; full exports stream every row.')
