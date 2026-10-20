"""Standalone local vector/table I/O. No QGIS, geopandas or ArcGIS dependency."""
from dataclasses import dataclass,field
from pathlib import Path
import csv,gzip,json,math,re,zipfile,xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from pyproj import CRS,Transformer,Geod
from shapely import from_wkb,from_wkt,transform as transform_geometry
from shapely.geometry import Point,LineString,Polygon,MultiPolygon,MultiPoint,MultiLineString,GeometryCollection,box
from shapely.geometry.polygon import orient

FILE_TYPES=[('GIS / tables','*.csv *.csv.gz *.shp *.gpkg *.kml *.kmz *.geojson *.json *.gdbtable *.gdb.zip *.zip *.fgb *.sqlite *.gml *.tab *.dbf *.parquet *.tif *.tiff'),('All files','*.*')]

@dataclass
class Dataset:
    frame: pd.DataFrame
    geometries: object = None
    metadata: dict = field(default_factory=dict)


def _pyogrio():
    try:import pyogrio;return pyogrio
    except ImportError as exc:raise RuntimeError('GIS support needs pyogrio. Run Start_Planner.bat or install requirements.txt in this standalone environment.') from exc


def source_path(path):
    path=Path(path).expanduser().resolve()
    if not path.exists():raise ValueError('Source does not exist: '+str(path))
    if path.is_dir() and path.suffix.lower()!='.gdb':raise ValueError('Select a complete .gdb directory, not its parent folder.')
    if path.suffix.lower()=='.shp':
        names={p.name.lower() for p in path.parent.iterdir()}
        if path.with_suffix('.dbf').name.lower() not in names:raise ValueError('Shapefile attributes require the matching .dbf file beside the .shp. Keep its companion files together.')
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            names=z.namelist();gdb=sorted({m.group(1) for n in names if (m:=re.search(r'(?i)(.*?\.gdb)(?:/|$)',n))})
            if len(gdb)==1:return '/vsizip/'+path.as_posix()+'/'+gdb[0]
            if not gdb and any(n.lower().endswith('.shp') for n in names):return '/vsizip/'+path.as_posix()
        raise ValueError('ZIP support expects a complete shapefile dataset or one .gdb folder. Extract other archives and select the actual dataset.')
    return str(path)


def _csv(path,nrows=None):
    for encoding in ('utf-8-sig','cp1252'):
        try:return pd.read_csv(path,dtype=str,encoding=encoding,nrows=nrows,keep_default_na=False).rename(columns=lambda x:x.strip())
        except UnicodeDecodeError:continue
    raise ValueError('CSV encoding could not be read; save it as UTF-8.')


def _is_csv(path):return str(path).lower().endswith(('.csv','.csv.gz'))
def _is_kml(path):return Path(path).suffix.lower() in ('.kml','.kmz')

def _tag(node):return node.tag.rsplit('}',1)[-1]
def _child_text(node,name):
    for c in node:
        if _tag(c)==name:return c.text or ''
    return ''

def _kml_geometry(node):
    kind=_tag(node)
    def coords(parent):
        text=' '.join(n.text or '' for n in parent.iter() if _tag(n)=='coordinates')
        return [(float(p.split(',')[0]),float(p.split(',')[1])) for p in text.split() if ',' in p]
    if kind=='Point':
        c=coords(node);return Point(c[0]) if c else None
    if kind=='LineString':return LineString(coords(node))
    if kind=='Polygon':
        outer=[];inners=[]
        for c in node:
            if _tag(c)=='outerBoundaryIs':outer=coords(c)
            elif _tag(c)=='innerBoundaryIs':inners.append(coords(c))
        return Polygon(outer,inners) if outer else None
    if kind=='MultiGeometry':
        parts=[g for c in node if (g:=_kml_geometry(c)) is not None]
        if not parts:return None
        types={g.geom_type for g in parts}
        if types=={'Polygon'}:return MultiPolygon(parts)
        if types=={'Point'}:return MultiPoint(parts)
        if types=={'LineString'}:return MultiLineString(parts)
        return GeometryCollection(parts)
    return None


def _kml_layers(path,cancelled=lambda:False):
    documents=[];path=Path(path)
    if path.suffix.lower()=='.kmz':
        with zipfile.ZipFile(path) as z:
            entries=[n for n in z.infolist() if n.filename.lower().endswith('.kml')]
            if sum(n.file_size for n in entries)>256*1024*1024:raise ValueError('KMZ expands beyond 256 MB of KML. Split it into regional/layer datasets.')
            documents=[(n.filename,z.read(n)) for n in entries]
    else:
        if path.stat().st_size>256*1024*1024:raise ValueError('KML exceeds 256 MB. Convert or split into regional datasets.')
        documents=[(path.name,path.read_bytes())]
    layers={};network_links=0;count=0
    def walk(node,folders,doc):
        nonlocal network_links,count
        tag=_tag(node)
        if tag=='NetworkLink':network_links+=1;return
        if tag in ('Folder','Document'):
            name=_child_text(node,'name').strip()
            if name:folders=folders+[name]
        if tag=='Placemark':
            count+=1
            if count%1000==0 and cancelled():raise InterruptedError('KML import cancelled.')
            props={'Name':_child_text(node,'name'),'Description':_child_text(node,'description'),'kml_id':node.attrib.get('id','')}
            for element in node.iter():
                t=_tag(element)
                if t=='Data' and element.attrib.get('name'):props[element.attrib['name']]=_child_text(element,'value')
                elif t=='SimpleData' and element.attrib.get('name'):props[element.attrib['name']]=element.text or ''
            geometry=None
            for c in node:
                geometry=_kml_geometry(c)
                if geometry is not None:break
            if geometry is None:raise ValueError('KML Placemark lacks supported Point/LineString/Polygon/MultiGeometry geometry: '+props['Name'])
            key=doc+'::'+('/'.join(folders) or 'Placemarks');layers.setdefault(key,[]).append((props,geometry))
            return
        for c in node:walk(c,folders,doc)
    for name,content in documents:walk(ET.fromstring(content),[],name)
    if not layers:raise ValueError('No local KML vector placemarks. NetworkLinks, imagery overlays and Google Earth streaming scenes are not downloaded.')
    return layers,network_links


def list_layers(path):
    source=source_path(path)
    if _is_csv(path):return [{'name':'CSV table','geometry_type':None}]
    if _is_kml(path):
        layers,_=_kml_layers(path)
        return [{'name':name,'geometry_type':' / '.join(sorted({g.geom_type for _,g in rows})),'features':len(rows)} for name,rows in layers.items()]
    if Path(path).suffix.lower()=='.json':
        if Path(path).stat().st_size>256*1024*1024:raise ValueError('JSON exceeds 256 MB; convert/clip to GPKG or another indexed vector format.')
        obj=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if isinstance(obj,list) or (isinstance(obj,dict) and isinstance(obj.get('records'),list)):
            return [{'name':'JSON records','geometry_type':None}]
    try:return [{'name':str(name),'geometry_type':None if typ is None else str(typ)} for name,typ in _pyogrio().list_layers(source)]
    except Exception as exc:
        suffix=' Select the complete .gdb folder with all companion files if this .gdbtable cannot be opened on its own.' if str(path).lower().endswith('.gdbtable') else ''
        raise ValueError(f'Cannot inspect vector dataset: {exc}.{suffix}') from exc


def _choose(path,layer):
    layers=list_layers(path);names=[v['name'] for v in layers]
    if layer:
        if layer not in names:raise ValueError('Layer not found: '+str(layer)+'. Available: '+', '.join(names))
        return layer
    if len(names)!=1:raise ValueError('Select a layer in Source layers / CRS. Available layers: '+', '.join(names))
    return names[0]


def _reproject(geometries,crs,override=None):
    source=override or crs
    if not source:raise ValueError('Spatial source has no declared CRS. Enter its actual source CRS (for example EPSG:32735) in Source layers / CRS.')
    source=CRS.from_user_input(source)
    if source!=CRS.from_epsg(4326):
        t=Transformer.from_crs(source,4326,always_xy=True)
        geometries=transform_geometry(geometries,lambda xy:np.column_stack(t.transform(xy[:,0],xy[:,1])))
    return geometries,source.to_string()


def iter_source(path,layer=None,crs_override=None,chunk_size=20000,nrows=None,bbox=None,cancelled=lambda:False,memory_mb=None):
    """Yield geometry in WGS84 and attributes. bbox uses west,south,east,north."""
    source=source_path(path)
    if cancelled():raise InterruptedError('Import cancelled.')
    if _is_csv(path):
        frame=_csv(path,nrows);geoms=None;meta={'driver':'CSV','layer':'CSV table','source':str(path),'source_crs':crs_override}
        for column in ['geometry','wkt','WKT','geom']:
            if column not in frame:continue
            values=frame[column].astype(str).str.strip();nonempty=values.ne('')
            if not nonempty.any() or not values[nonempty].str.match(r'^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(',case=False).all():continue
            crs=crs_override or ('EPSG:4326' if {'latitude','longitude','confidence'}.issubset(frame.columns) or 'full_plus_code' in frame else None)
            geoms=from_wkt(np.array([v if v else None for v in values],dtype=object));geoms,crs=_reproject(geoms,crs)
            meta['source_crs']=crs;meta['coordinate_crs']='EPSG:4326';break
        yield Dataset(frame,geoms,meta);return
    if _is_kml(path):
        layers,network=_kml_layers(path,cancelled);name=_choose(path,layer);rows=layers[name]
        if crs_override and CRS.from_user_input(crs_override)!=CRS.from_epsg(4326):raise ValueError('KML coordinates are WGS84 by definition; do not override KML to a projected CRS.')
        if nrows is not None:rows=rows[:nrows]
        for start in range(0,len(rows),chunk_size):
            if cancelled():raise InterruptedError('KML import cancelled.')
            part=rows[start:start+chunk_size];frame=pd.DataFrame([r for r,_ in part]).fillna('');geoms=np.array([g for _,g in part],dtype=object)
            if bbox:
                mask=np.array([g.intersects(box(*bbox)) for g in geoms]);frame=frame.loc[mask].reset_index(drop=True);geoms=geoms[mask]
            yield Dataset(frame,geoms,{'driver':'KML','layer':name,'source':str(path),'source_crs':'EPSG:4326','network_links_not_followed':network})
        return
    if Path(path).suffix.lower()=='.json':
        if Path(path).stat().st_size>256*1024*1024:raise ValueError('JSON exceeds 256 MB; convert/clip to GPKG or another indexed vector format.')
        obj=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if isinstance(obj,list) or (isinstance(obj,dict) and isinstance(obj.get('records'),list)):
            frame=pd.DataFrame(obj if isinstance(obj,list) else obj['records']).fillna('')
            if nrows is not None:frame=frame.head(nrows)
            yield Dataset(frame,None,{'driver':'JSON records','layer':'JSON records','source':str(path),'source_crs':crs_override});return
    ogr=_pyogrio();name=_choose(path,layer);info=ogr.read_info(source,layer=name)
    crs=crs_override or info.get('crs');has_geometry=info.get('geometry_type') not in (None,'None')
    if has_geometry and not crs:raise ValueError('The GIS source is missing its CRS. Set its actual source CRS before importing.')
    source_bbox=None
    if bbox and has_geometry:
        t=Transformer.from_crs(4326,crs,always_xy=True);source_bbox=t.transform_bounds(*bbox,densify_pts=21)
    maximum=chunk_size
    if memory_mb:chunk_size=min(chunk_size,1000)
    skip=0
    while nrows is None or skip<nrows:
        if cancelled():raise InterruptedError('GIS import cancelled.')
        size=chunk_size if nrows is None else min(chunk_size,nrows-skip)
        meta,fids,wkb,arrays=ogr.raw.read(source,layer=name,force_2d=True,return_fids=True,skip_features=skip,max_features=size,bbox=source_bbox,datetime_as_string=True)
        count=len(fids)
        if not count:break
        frame=pd.DataFrame({str(col):data for col,data in zip(meta['fields'],arrays)})
        types=info.get('ogr_types',[])
        for col,typ in zip(info['fields'],types):
            if 'Integer' in str(typ) and col in frame:
                frame[col]=['' if pd.isna(v) else str(int(v)) for v in frame[col]]
        frame=frame.astype(object).where(pd.notna(frame),'');frame['__source_fid']=[str(v) for v in fids]
        geoms=from_wkb(wkb) if wkb is not None else None
        if geoms is not None:geoms,actual_crs=_reproject(geoms,meta.get('crs') or info.get('crs'),crs_override)
        else:actual_crs=crs
        if bbox and geoms is not None:
            mask=np.array([g is not None and not g.is_empty and g.intersects(box(*bbox)) for g in geoms]);frame=frame.loc[mask].reset_index(drop=True);geoms=geoms[mask]
        yield Dataset(frame,geoms,{'driver':info['driver'],'layer':name,'source':str(path),'source_crs':actual_crs,'coordinate_crs':'EPSG:4326' if geoms is not None else None})
        skip+=count
        if count<size:break
        if memory_mb and len(frame):
            estimated=frame.memory_usage(deep=True).sum()+sum(len(g.wkb)*3 for g in geoms if g is not None) if geoms is not None else frame.memory_usage(deep=True).sum()
            per_row=max(1,estimated/len(frame));chunk_size=max(100,min(maximum,chunk_size*2,int(memory_mb*1024*1024/(per_row*4))))


def read_source(path,layer=None,crs_override=None,nrows=None,bbox=None,cancelled=lambda:False,max_records=1000000):
    pieces=[];geometry=[];spatial=False;metadata={};count=0
    for data in iter_source(path,layer,crs_override,nrows=(max_records+1 if nrows is None and _is_csv(path) else nrows),bbox=bbox,cancelled=cancelled):
        count+=len(data.frame)
        if count>max_records:raise ValueError(f'Source exceeds {max_records:,} records. Select a smaller layer or clip the data first.')
        pieces.append(data.frame);metadata=data.metadata
        if data.geometries is not None:spatial=True;geometry.extend(data.geometries)
    frame=pd.concat(pieces,ignore_index=True) if pieces else pd.DataFrame()
    return Dataset(frame,np.array(geometry,dtype=object) if spatial else None,metadata)


def match_mapping(frame,kind,current,defaults):
    """Preserve valid explicit choices; suggest only unambiguous known aliases."""
    cols=list(frame.columns);result=dict(current or defaults);notes=[]
    aliases={'country':['opco','opco_id','country','country_code'], 'id':['grid_id','GridID'] if kind in ('grid','population') else ['candidate_id','point_id','physical_site_id','siteid','site_id','reference_id','exclusion_id'],
      'lat':['latitude','lat','centroid_lat','centroid_latitude'],'lon':['longitude','lon','long','centroid_long','centroid_longitude'],
      'operator':['operator','operator_name','network','opco_comp'],'size':['grid_size','resolution'],
      'category':['combined_category_wf','combined_category','category'],'period':['month','db_period','period'],
      'value':['population','population_count','population_density','nga_general_2020_sum'],'technology':['technology','tech'],
      'status':['physical_site_status','status'],'grid_id':['grid_id'],'feasible':['feasible'],
      'mr':['total_mr_count'],'area':['area_km2_actual'],'coverage':['coverage_category'],'speed':['throughput_category']}
    for role,col in list(result.items()):
        if col in cols:continue
        targets=[col.lower()]+[a.lower() for a in aliases.get(role,[])] if col else [a.lower() for a in aliases.get(role,[])]
        found=[c for c in cols if re.sub(r'_\d+$','',c).lower() in targets]
        if role=='operator' and len(found)>1:
            def score(c):
                values=frame[c].astype(str).str.strip();nonempty=values.ne('')
                return float((~values[nonempty].str.contains(r'^[A-Za-z]{2}_',regex=True)).mean()) if nonempty.any() else 0
            scored=sorted([(score(c),c) for c in found],reverse=True)
            found=[scored[0][1]] if len(scored)==1 or scored[0][0]>scored[1][0] else found
        if len(found)==1:result[role]=found[0];notes.append(f'{kind}.{role} mapped to {found[0]}')
    return result,notes


def normalize_role(data,kind,mapping,options=None):
    """Derive coordinates from geometry, keep real polygons and actual areas."""
    options=options or {};mapping=dict(mapping);frame=data.frame.copy();polygons={};geoms=data.geometries
    frame=frame.astype(object).where(pd.notna(frame),'')
    use_geometry=options.get('geometry_coordinates',True)
    if geoms is not None and use_geometry and kind!='population':
        lat=[];lon=[]
        for g in geoms:
            if g is None or g.is_empty:lat.append('');lon.append('');continue
            if kind=='grid' and g.geom_type in ('Polygon','MultiPolygon'):p=g.centroid
            elif g.geom_type=='Point':p=g
            else:raise ValueError(f'{kind}: use single Point geometry for sites/demand/reference roles or Polygon geometry for grids; found {g.geom_type}.')
            lat.append(p.y);lon.append(p.x)
        frame['__ns_lat']=lat;frame['__ns_lon']=lon;mapping.update(lat='__ns_lat',lon='__ns_lon')
    elif geoms is None and options.get('crs_override') and kind!='population' and mapping.get('lat') in frame and mapping.get('lon') in frame:
        x=pd.to_numeric(frame[mapping['lon']],errors='coerce');y=pd.to_numeric(frame[mapping['lat']],errors='coerce')
        t=Transformer.from_crs(options['crs_override'],4326,always_xy=True);lon,lat=t.transform(x.to_numpy(),y.to_numpy())
        frame['__ns_lat']=lat;frame['__ns_lon']=lon;mapping.update(lat='__ns_lat',lon='__ns_lon')
    if kind=='grid' and geoms is not None and any(g is not None and g.geom_type in ('Polygon','MultiPolygon') for g in geoms):
        if not all(g is not None and g.geom_type in ('Polygon','MultiPolygon') and not g.is_empty and g.is_valid for g in geoms):raise ValueError('Grid geometry must consist of valid polygons; fix invalid/mixed/null geometries before planning.')
        geod=Geod(ellps='WGS84');areas=[]
        for i,g in enumerate(geoms):
            parts=list(g.geoms) if g.geom_type=='MultiPolygon' else [g]
            areas.append(sum(abs(geod.geometry_area_perimeter(orient(p,sign=1))[0]) for p in parts)/1e6)
            gid=str(frame.iloc[i][mapping['id']]).strip();country=str(frame.iloc[i][mapping['country']]).strip().upper()
            period=str(frame.iloc[i][mapping['period']]).strip();size=str(frame.iloc[i][mapping['size']]).lower().replace(' ','')
            key=(country,gid,period,size)
            if key in polygons:raise ValueError('Duplicate polygon key in source: '+str(key))
            polygons[key]=g
        frame['__ns_area_km2']=areas;mapping['area']='__ns_area_km2'
    return frame,mapping,polygons


def driver_report():
    p=_pyogrio();return {'pyogrio':p.__version__,'gdal':p.__gdal_version_string__,'drivers':p.list_drivers()}
