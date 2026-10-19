"""Persistent SQLite/RTree footprints; no national-scale geometry list in RAM."""
import hashlib,json,math,os,sqlite3,tempfile
from contextlib import closing
from collections import deque
from pathlib import Path
from dataclasses import replace
import numpy as np
import pandas as pd
from shapely.geometry import shape,mapping
from pyproj import Geod

class DiskFeatures:
    def __init__(self,owner):self.owner=owner
    def __len__(self):return self.owner.count
    def __iter__(self):return self.owner.iter_features()
    def __getitem__(self,key):
        if isinstance(key,slice):
            start,stop,step=key.indices(len(self))
            if step!=1:raise ValueError('Only consecutive footprint preview slices are supported.')
            return list(self.owner.iter_features(limit=max(0,stop-start),offset=start))
        return next(self.owner.iter_features(limit=1,offset=key))

class IndexWriter:
    def __init__(self,target):
        self.target=Path(target);self.target.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=self.target.stem+'_',suffix='.partial',dir=self.target.parent);os.close(fd);self.temp=Path(name)
        self.db=sqlite3.connect(name);self.db.execute('PRAGMA cache_size=-16384')
        self.db.executescript('CREATE TABLE footprints(id INTEGER PRIMARY KEY, digest BLOB UNIQUE, feature TEXT); CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy); CREATE TABLE metadata(value TEXT);')
        self.count=0;self.recent=set();self.recent_order=deque()
    def add(self,geom,props):
        digest=hashlib.sha256(geom.normalize().wkb).digest()
        if digest in self.recent:return
        self.recent.add(digest);self.recent_order.append(digest)
        if len(self.recent_order)>4096:self.recent.discard(self.recent_order.popleft())
        feature=json.dumps({'type':'Feature','geometry':mapping(geom),'properties':props},allow_nan=False,default=str)
        cur=self.db.execute('INSERT OR IGNORE INTO footprints(digest,feature) VALUES(?,?)',(digest,feature))
        if cur.rowcount:
            w,s,e,n=geom.bounds;self.db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(cur.lastrowid,w,e,s,n));self.count+=1
        if self.count and self.count%5000==0:self.db.commit()
    def finish(self,metadata):
        metadata={**metadata,'storage':'SQLite RTree disk index','footprint_count':self.count,'index_schema':1}
        self.db.execute('INSERT INTO metadata VALUES(?)',(json.dumps(metadata,default=str),));self.db.commit();self.db.close();os.replace(self.temp,self.target)
        return DiskBuildingContext(self.target)
    def abort(self):
        try:self.db.close()
        finally:self.temp.unlink(missing_ok=True)

class DiskBuildingContext:
    def __init__(self,path,cancelled=lambda:False,log=lambda s:None):
        self.path=Path(path);self.cancelled=cancelled;self.log=log;self.issues=[]
        with closing(self.connect()) as db:
            self.metadata=json.loads(db.execute('SELECT value FROM metadata').fetchone()[0]);self.count=int(self.metadata['footprint_count'])
            b=db.execute('SELECT min(minx),min(miny),max(maxx),max(maxy) FROM bounds').fetchone();self.bounds=list(b)
        if not self.count:raise ValueError('No usable building footprints in disk index.')
        self.metadata['index_path']=str(self.path.resolve());self.features=DiskFeatures(self)
    def connect(self):
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA cache_size=-8192');return db
    def iter_features(self,bbox=None,limit=None,offset=0):
        query='SELECT f.feature FROM footprints f';params=[]
        if bbox:
            w,s,e,n=bbox;query+=' JOIN bounds b ON f.id=b.id WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?';params=[w,e,s,n]
        query+=' ORDER BY f.id'
        if limit is not None:query+=' LIMIT ? OFFSET ?';params.extend([limit,offset])
        with closing(self.connect()) as db:
            cursor=db.execute(query,params)
            while True:
                if self.cancelled():raise InterruptedError('Building spatial query cancelled.')
                batch=cursor.fetchmany(1000)
                if not batch:break
                for row in batch:yield json.loads(row[0])
    def annotate(self,cand,rules):
        from buildings import BuildingContext
        numeric=['mapped_buildings_within_radius','mapped_footprint_area_m2','nearest_mapped_building_m','containing_building_area_m2']
        for key in numeric:cand[key]=0. if key in numeric[:2] else np.nan
        cand['containing_building_id']='';cand['building_factor']=1.;cand['building_context']='No mapped evidence within radius; completeness unknown'
        # Nearby candidates share a query; query results are streamed in batches of 1,000.
        grouped=cand.groupby([np.floor(cand.lon/.1),np.floor(cand.lat/.1)]).groups
        groups=[]
        for indices in grouped.values():
            ids=list(indices);groups.extend(ids[start:start+256] for start in range(0,len(ids),256))
        radius=max(rules.building_context_radius_m,rules.building_clearance_m)+2
        if radius>100000:raise ValueError('Building clearance above 100 km is not supported by local metric screening.')
        context_rules=replace(rules,building_rule='Context only')
        for step,indices in enumerate(groups,1):
            if self.cancelled():raise InterruptedError('Building screening cancelled.')
            ids=list(indices);part=cand.loc[ids].copy().reset_index(drop=True)
            west,south,east,north=part.lon.min(),part.lat.min(),part.lon.max(),part.lat.max()
            cx=(west+east)/2;cy=(south+north)/2
            # Conservative metric-to-angular bounds; no missing neighbours at tile edges.
            lat_margin=radius/110000;edge_lat=min(89.999,max(abs(south),abs(north))+lat_margin)
            lon_margin=min(360,radius/(110000*max(.00001,math.cos(math.radians(edge_lat)))))
            bbox=(max(-180,west-lon_margin),max(-90,south-lat_margin),min(180,east+lon_margin),min(90,north+lat_margin))
            # Longitude-wrapping AOIs query all longitudes to retain seam neighbours.
            if west-lon_margin < -180 or east+lon_margin > 180:bbox=(-180,bbox[1],180,bbox[3])
            batch=[]
            def apply_batch(features):
                context=BuildingContext(features,self.metadata,projection_center=(cy,cx));probe=part.copy();context.annotate(probe,context_rules)
                for key in numeric[:2]:part[key]+=probe[key].to_numpy()
                old=part.nearest_mapped_building_m.to_numpy();new=probe.nearest_mapped_building_m.to_numpy();part['nearest_mapped_building_m']=np.fmin(old,new)
                mask=probe.containing_building_area_m2.notna() & (part.containing_building_area_m2.isna() | probe.containing_building_area_m2.ge(part.containing_building_area_m2))
                for key in ('containing_building_id','containing_building_area_m2'):part.loc[mask,key]=probe.loc[mask,key]
            for feature in self.iter_features(bbox):
                batch.append(feature)
                if len(batch)>=1000:apply_batch(batch);batch=[]
            if batch:apply_batch(batch)
            for key in numeric+['containing_building_id']:cand.loc[ids,key]=part[key].to_numpy()
            if step%50==0:self.log(f'Building screening: {step:,}/{len(groups):,} spatial batches')
        counts=cand.mapped_buildings_within_radius.to_numpy();cand['mapped_buildings_within_radius']=counts.astype(int)
        cand['building_context']=np.where(counts>0,'Mapped building evidence within context radius','No mapped evidence within radius; completeness unknown')
        cand['building_factor']=1+rules.building_density_boost*np.minimum(np.log1p(counts)/np.log(101),1)
        if rules.building_rule=='Ground clearance':cand.loc[cand.nearest_mapped_building_m.le(rules.building_clearance_m),'rejection']='Inside/too close to mapped footprint for ground-site rule'
        elif rules.building_rule=='Rooftop search':cand.loc[cand.containing_building_id.eq('')|cand.containing_building_area_m2.lt(rules.min_roof_area_m2),'rejection']='No containing mapped footprint meeting minimum roof-area proxy'
    def roof_candidates(self,g):
        from planner import xyz,km_from_chord
        from scipy.spatial import cKDTree
        rows=[];tree=cKDTree(xyz(g));side=1000 if g['size'].iloc[0].lower()=='1km' else 3000
        def collect(features):
            points=[shape(f['geometry']).representative_point() for f in features]
            frame=pd.DataFrame({'lat':[p.y for p in points],'lon':[p.x for p in points]})
            distances,indices=tree.query(xyz(frame),k=1);distances=km_from_chord(distances)
            for i,point in enumerate(points):
                if distances[i]>side/1000/np.sqrt(2)+.01:continue
                props=features[i]['properties'];parent=g.iloc[indices[i]]
                rows.append({'source_candidate_id':'ROOF:'+str(props['building_id']),'grid_id':parent.id,'country':parent.country,
                    'lat':point.y,'lon':point.x,'feasible':'unknown','suitability':1.,'survey_verified':'No','accuracy_m':np.nan,
                    'location_source':str(props.get('source','Open footprint input')),'location_basis':'Footprint interior point; roof structurally unverified','rejection':''})
                if len(rows)>self.max_candidates:raise ValueError('Rooftop alternatives exceed the configured candidate limit; use a smaller planning area or supplied candidates.')
        batch=[]
        for feature in self.iter_features():
            batch.append(feature)
            if len(batch)>=1000:collect(batch);batch=[]
        if batch:collect(batch)
        if not rows:raise ValueError('No building roof search points match the selected grid scope.')
        return pd.DataFrame(rows)
    def export_geojson(self,path):
        with Path(path).open('w',encoding='utf-8') as f:
            f.write('{"type":"FeatureCollection","metadata":'+json.dumps(self.metadata)+',"features":[')
            for i,feature in enumerate(self.iter_features()):
                if i:f.write(',')
                f.write(json.dumps(feature,allow_nan=False))
            f.write(']}')
