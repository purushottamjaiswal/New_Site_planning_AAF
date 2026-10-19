"""Open footprint ingestion, bounded OSM retrieval and evidence-based screening."""
from pathlib import Path
from datetime import datetime,timezone
import csv,gzip,json,math,sqlite3,urllib.request,urllib.parse
import numpy as np
import pandas as pd
from shapely.geometry import shape,mapping,Polygon,LineString,box
from shapely import points as shape_points
from shapely import wkt,transform as transform_geometry
from shapely.ops import polygonize,unary_union
from shapely.strtree import STRtree
from pyproj import CRS,Transformer

OSM_ENDPOINT='https://overpass-api.de/api/interpreter'
GOOGLE_BUILDINGS='https://sites.research.google/gr/open-buildings/'


def area_bbox(lat,lon,radius_km):
    lat=float(lat);lon=float(lon);radius_km=float(radius_km)
    if not (-80<lat<80 and -180<lon<180 and .1<=radius_km<=5):
        raise ValueError('Use a valid centre and an area radius of 0.1–5 km for a single OSM request.')
    dy=radius_km/111.195;dx=dy/math.cos(math.radians(lat))
    return [lat-dy,lon-dx,lat+dy,lon+dx]


def overpass_to_features(data):
    if data.get('remark'):raise ValueError('Overpass returned an incomplete/error response: '+str(data['remark']))
    features=[];seen=set();unsupported=0
    for e in data.get('elements',[]):
        tags=e.get('tags',{});geom=None
        if tags.get('building') in (None,'no'):continue
        if e.get('type')=='way':
            xy=[(p['lon'],p['lat']) for p in e.get('geometry',[]) if 'lon'in p and 'lat'in p]
            if len(xy)>=4 and xy[0]==xy[-1]:geom=Polygon(xy)
        elif e.get('type')=='relation':
            outer=[];inner=[]
            for member in e.get('members',[]):
                xy=[(p['lon'],p['lat']) for p in member.get('geometry',[]) if 'lon'in p and 'lat'in p]
                if len(xy)>=2 and member.get('type')=='way':
                    (inner if member.get('role')=='inner' else outer).append(LineString(xy))
            if outer:
                rings=list(polygonize(unary_union(outer)))
                if rings:
                    geom=unary_union(rings)
                    holes=list(polygonize(unary_union(inner))) if inner else []
                    if holes:geom=geom.difference(unary_union(holes))
        if geom is None or geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):
            unsupported+=1;continue
        identity=geom.normalize().wkb
        if identity in seen:continue
        seen.add(identity)
        features.append({'type':'Feature','geometry':mapping(geom),'properties':{
          **tags,'building_id':f"osm_{e['type']}_{e['id']}",'source':'OpenStreetMap contributors',
          'source_url':f"https://www.openstreetmap.org/{e['type']}/{e['id']}",'license':'ODbL 1.0'}})
    return features,unsupported


def fetch_osm(lat,lon,radius_km,path):
    bbox=area_bbox(lat,lon,radius_km);s,w,n,e=bbox
    query=f'[out:json][timeout:60];(way["building"]["building"!="no"]({s},{w},{n},{e});relation["building"]["building"!="no"]({s},{w},{n},{e}););out tags geom;'
    request=urllib.request.Request(OSM_ENDPOINT,data=urllib.parse.urlencode({'data':query}).encode(),headers={
      'User-Agent':'NewSitePlanningTool/3.0 (local desktop; bounded building-area query)',
      'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request,timeout=80) as response:
        payload=response.read(30_000_001)
    if len(payload)>30_000_000:raise ValueError('OSM response exceeded 30 MB. Use a smaller area.')
    features,unsupported=overpass_to_features(json.loads(payload))
    if not features:raise ValueError('No complete valid OSM building footprints returned. No-data is not evidence of no buildings.')
    result={'type':'FeatureCollection','metadata':{'source':'OpenStreetMap contributors','license':'ODbL 1.0',
      'source_url':'https://www.openstreetmap.org/copyright','request_bbox':bbox,
      'retrieved_utc':datetime.now(timezone.utc).isoformat(),'unsupported_features':unsupported,
      'completeness':'Unknown; mapped buildings only. OSM dates and coverage vary.'},'features':features}
    Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_text(json.dumps(result),encoding='utf-8')
    return len(features)


class BuildingContext:
    def __init__(self,features,metadata,issues=None,projection_center=None):
        self.metadata=metadata;self.issues=issues or [];self.features=features
        if not features:raise ValueError('No usable building footprints after geometry/confidence/area filtering.')
        self.geoms=[shape(f['geometry']) for f in features]
        bounds=np.array([g.bounds for g in self.geoms]);w,s=np.min(bounds[:,:2],axis=0);e,n=np.max(bounds[:,2:],axis=0)
        self.bounds=[float(w),float(s),float(e),float(n)]
        # Clip regional data before importing; avoid applying metre buffers in degrees.
        width=(e-w)*111.195*math.cos(math.radians((n+s)/2));height=(n-s)*111.195
        if projection_center is None and (width>200 or height>200):raise ValueError('Footprints span over 200 km. Clip to a regional/town AOI first, or set the building clip bbox before importing country-scale CSV data.')
        cy,cx=projection_center or ((s+n)/2,(w+e)/2)
        self.crs=CRS.from_proj4(f'+proj=aeqd +lat_0={cy} +lon_0={cx} +datum=WGS84 +units=m')
        self.to_m=Transformer.from_crs('EPSG:4326',self.crs,always_xy=True)
        self.metric=transform_geometry(np.asarray(self.geoms,dtype=object),lambda xy:np.column_stack(self.to_m.transform(xy[:,0],xy[:,1]))).tolist()
        self.tree=STRtree(self.metric);self.areas=np.array([g.area for g in self.metric])
        self.ids=[str(f['properties'].get('building_id',i)) for i,f in enumerate(features)]
        for f,area in zip(self.features,self.areas):f['properties']['computed_area_m2']=float(area)

    def roof_candidates(self,g):
        from planner import xyz,chord,km_from_chord
        from scipy.spatial import cKDTree
        rows=[];tree=cKDTree(xyz(g));side=1000 if g['size'].iloc[0].lower()=='1km' else 3000
        rep=[geom.representative_point() for geom in self.geoms]
        frame=pd.DataFrame({'lat':[p.y for p in rep],'lon':[p.x for p in rep]})
        d,idx=tree.query(xyz(frame),k=1);dist=km_from_chord(d)
        for i,p in enumerate(rep):
            if dist[i]>side/1000/np.sqrt(2)+.01:continue
            rows.append({'source_candidate_id':'ROOF:'+self.ids[i],'grid_id':g.iloc[idx[i]].id,
              'country':g.iloc[idx[i]].country,'lat':p.y,'lon':p.x,'feasible':'unknown','suitability':1.,
              'survey_verified':'No','accuracy_m':np.nan,'location_source':str(self.features[i]['properties'].get('source','Open footprint input')),
              'location_basis':'Footprint interior point; roof structurally unverified','rejection':''})
        if not rows:raise ValueError('No building roof search points match the selected grid scope.')
        return pd.DataFrame(rows)

    def annotate(self,cand,rules):
        if not len(cand):
            for key in ['mapped_buildings_within_radius','mapped_footprint_area_m2','nearest_mapped_building_m','containing_building_area_m2','building_factor']:cand[key]=pd.Series(dtype=float)
            cand['building_context']='';cand['containing_building_id']='';return
        x,y=self.to_m.transform(cand.lon.to_numpy(float),cand.lat.to_numpy(float));pts=shape_points(x,y)
        radius=rules.building_context_radius_m
        pairs=self.tree.query(pts,predicate='dwithin',distance=radius)
        counts=np.bincount(pairs[0],minlength=len(cand)) if pairs.size else np.zeros(len(cand))
        areas=np.bincount(pairs[0],weights=self.areas[pairs[1]],minlength=len(cand)) if pairs.size else np.zeros(len(cand))
        cand['mapped_buildings_within_radius']=counts.astype(int);cand['mapped_footprint_area_m2']=areas
        cand['nearest_mapped_building_m']=np.nan
        indexes,distances=self.tree.query_nearest(pts,max_distance=max(radius,rules.building_clearance_m)+1,all_matches=False,return_distance=True)
        if indexes.size:cand.loc[indexes[0],'nearest_mapped_building_m']=distances
        cand['containing_building_id']='';cand['containing_building_area_m2']=np.nan
        inside=self.tree.query(pts,predicate='covered_by')
        if inside.size:
            # Sort ascending so an overlapping larger footprint is the final selected context.
            order=np.argsort(self.areas[inside[1]])
            for i,j in inside[:,order].T:
                cand.loc[i,'containing_building_id']=self.ids[j];cand.loc[i,'containing_building_area_m2']=self.areas[j]
        cand['building_context']=np.where(counts>0,'Mapped building evidence within context radius','No mapped evidence within radius; completeness unknown')
        cand['building_factor']=1+rules.building_density_boost*np.minimum(np.log1p(counts)/np.log(101),1)
        if rules.building_rule=='Ground clearance':
            blocked=cand.nearest_mapped_building_m.le(rules.building_clearance_m)
            cand.loc[blocked,'rejection']='Inside/too close to mapped footprint for ground-site rule'
        elif rules.building_rule=='Rooftop search':
            blocked=cand.containing_building_id.eq('')|cand.containing_building_area_m2.lt(rules.min_roof_area_m2)
            cand.loc[blocked,'rejection']='No containing mapped footprint meeting minimum roof-area proxy'
        # Footprint area is not usable roof area. No structural or ownership approval inferred.


def _read_buildings(path,rules,cancelled=lambda:False,layer=None,crs_override=None,sink=None,log=lambda s:None):
    path=Path(path);features=[];issues=[];metadata={'source_file':path.name,'completeness':'Unknown'}
    clip=None
    if rules.building_clip_bbox.strip():
        try:
            s,w,n,e=map(float,rules.building_clip_bbox.split(','));clip=(w,s,e,n)
            if not (-90<=s<n<=90 and -180<=w<e<=180):raise ValueError()
        except Exception:raise ValueError('Building clip bbox must be south,west,north,east in WGS84.')
    def accept(geometry,props,row):
        metadata['source_records_processed']=metadata.get('source_records_processed',0)+1
        try:
            geom=wkt.loads(geometry) if isinstance(geometry,str) else shape(geometry)
            if isinstance(geometry,str) and crs_override and CRS.from_user_input(crs_override)!=CRS.from_epsg(4326):
                t=Transformer.from_crs(crs_override,4326,always_xy=True)
                geom=transform_geometry(geom,lambda xy:np.column_stack(t.transform(xy[:,0],xy[:,1])))
            if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):raise ValueError('Invalid/non-polygon footprint')
            w,s,e,n=geom.bounds
            if not (-180<=w<=e<=180 and -90<=s<=n<=90):raise ValueError('Footprint is not valid WGS84 longitude/latitude')
            if clip and not geom.intersects(box(*clip)):return
            confidence=props.get('confidence')
            if confidence not in (None,''):
                confidence=float(confidence)
                if not math.isfinite(confidence) or not 0<=confidence<=1:raise ValueError('Invalid confidence')
                if confidence<rules.building_min_confidence:
                    metadata['confidence_filtered']=metadata.get('confidence_filtered',0)+1;return
            props=dict(props);props.setdefault('building_id',str(props.get('full_plus_code') or props.get('id') or f'building_{row}'))
            props.setdefault('source',metadata.get('source','User-supplied open building dataset'))
            props.setdefault('license',metadata.get('license','Verify source license'))
            if sink:sink(geom,props)
            else:features.append({'type':'Feature','geometry':mapping(geom),'properties':props})
            if not sink and len(features)>200000:raise OverflowError('More than 200,000 regional footprints; clip the dataset further.')
        except (OverflowError,sqlite3.Error,OSError,MemoryError):raise
        except Exception as exc:
            metadata['invalid_footprints']=metadata.get('invalid_footprints',0)+1
            if len(issues)<1000:issues.append({'source':'buildings','source_row':row,'id':str(props.get('building_id','')),'reason':str(exc)})
    if path.name.lower().endswith(('.csv','.csv.gz')):
        metadata.update({'source':'Google Open Buildings / supplied WKT CSV','source_url':GOOGLE_BUILDINGS,
                         'license':'Verify original source; Google Open Buildings offers CC BY 4.0 or ODbL 1.0'})
        opener=gzip.open if path.name.lower().endswith('.gz') else open
        with opener(path,'rt',encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or 'geometry' not in reader.fieldnames:raise ValueError('Building CSV requires geometry WKT polygons, not centroid-only data.')
            for row_no,row in enumerate(reader,2):
                if row_no%1000==0 and cancelled():raise InterruptedError('Building import cancelled.')
                if row_no%100000==0:log(f'Building CSV scan: {row_no:,} records')
                accept(row.get('geometry',''),{k:v for k,v in row.items() if k!='geometry'},row_no)
    else:
        from gis_io import iter_source
        from building_stream import building_chunks
        if path.suffix.lower() in ('.geojson','.json') and path.stat().st_size<=2*1024*1024:
            try:
                obj=json.loads(path.read_text(encoding='utf-8-sig'))
                if isinstance(obj,dict):metadata.update(obj.get('metadata',{}))
            except (ValueError,UnicodeDecodeError):pass
        offset=0
        for data in building_chunks(path,layer=layer,crs_override=crs_override,bbox=clip,cancelled=cancelled):
            metadata.update({k:v for k,v in data.metadata.items() if k not in ('source',)})
            metadata.setdefault('source','User-supplied open building dataset')
            if data.geometries is None and 'geometry' not in data.frame:
                raise ValueError('Selected building layer has no polygon geometry/WKT. Choose a building feature class, not a non-spatial or system table.')
            rows=data.frame.to_dict('records')
            for i,props in enumerate(rows):
                if (offset+i)%1000==0 and cancelled():raise InterruptedError('Building import cancelled.')
                geom=data.geometries[i] if data.geometries is not None else None
                if geom is not None:accept(mapping(geom),props,offset+i+1)
                elif isinstance(props.get('geometry'),str):accept(props.pop('geometry'),props,offset+i+1)
                else:accept(None,props,offset+i+1)
            offset+=len(rows)
            log(f'Building import: {offset:,} source records processed')
    if sink:return metadata,issues
    # Remove repeated geometry without doubling mapped density; retain first provenance.
    unique=[];seen=set()
    for feature in features:
        key=shape(feature['geometry']).normalize().wkb
        if key not in seen:seen.add(key);unique.append(feature)
    return BuildingContext(unique,metadata,issues)


def load_buildings(path,rules,cancelled=lambda:False,layer=None,crs_override=None,disk=False,cache_dir=None,log=lambda s:None):
    if not disk:return _read_buildings(path,rules,cancelled,layer,crs_override)
    import hashlib
    from planner import source_fingerprint
    from building_index import IndexWriter,DiskBuildingContext
    identity={'schema':1,'source_path':str(Path(path).resolve()),'fingerprint':source_fingerprint(path),'layer':layer,'crs':crs_override,'clip':rules.building_clip_bbox,'minimum_confidence':rules.building_min_confidence}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    folder=Path(cache_dir) if cache_dir else Path(__file__).resolve().parent/'building_cache'
    target=folder/(key+'.sqlite')
    if cancelled():raise InterruptedError('Building import cancelled.')
    if target.exists():
        log('Reusing completed building disk index.')
        context=DiskBuildingContext(target,cancelled,log)
    else:
        log('Building a reusable disk index; large files are scanned in chunks. First import can take time.')
        writer=IndexWriter(target)
        try:
            metadata,issues=_read_buildings(path,rules,cancelled,layer,crs_override,sink=writer.add,log=log)
            if not writer.count:raise ValueError('No usable building footprints after filtering.')
            context=writer.finish({**metadata,'cache_identity':identity,'issue_examples':issues})
            context.cancelled=cancelled;context.log=log
        except BaseException:
            writer.abort();raise
    context.issues=context.metadata.get('issue_examples',[]);context.max_candidates=rules.max_candidate_points
    log(f'{context.count:,} unique footprint polygons available on disk; nearby footprints are queried during screening.')
    return context
