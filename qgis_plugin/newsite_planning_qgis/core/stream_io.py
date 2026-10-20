"""Bounded table/vector streams for temporary database ingestion."""
from pathlib import Path
import csv,gzip,codecs,json,zipfile,xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from shapely import from_wkt
from gis_io import Dataset,iter_source,_reproject,_kml_geometry,_child_text,_tag


def csv_chunks(path,batch_size=2000,nrows=None,cancelled=lambda:False):
    opener=gzip.open if str(path).lower().endswith('.gz') else open
    encoding='utf-8-sig';decoder=codecs.getincrementaldecoder(encoding)()
    try:
        with opener(path,'rb') as f:
            while True:
                if cancelled():raise InterruptedError('CSV import cancelled.')
                b=f.read(1024*1024)
                if not b:decoder.decode(b,final=True);break
                decoder.decode(b)
                if nrows is not None:break
    except UnicodeDecodeError:encoding='cp1252'
    with opener(path,'rt',encoding=encoding,newline='') as f:
        sample=f.read(65536);f.seek(0)
        try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
        except csv.Error:dialect=csv.excel
        reader=csv.reader(f,dialect,strict=True);columns=[c.strip() for c in next(reader,[])]
        if not columns or len(columns)!=len(set(columns)):raise ValueError('CSV has missing or duplicate column headers.')
        rows=[];count=0
        for row in reader:
            if count%1000==0 and cancelled():raise InterruptedError('CSV import cancelled.')
            if not row:continue
            if len(row)!=len(columns):raise ValueError(f'CSV row {reader.line_num} has {len(row)} fields; expected {len(columns)}.')
            rows.append(row);count+=1
            if len(rows)>=batch_size:yield pd.DataFrame(rows,columns=columns);rows=[]
            if nrows is not None and count>=nrows:break
        if rows:yield pd.DataFrame(rows,columns=columns)
        elif count==0:yield pd.DataFrame(columns=columns)


def kml_records(path,cancelled=lambda:False,names_only=False):
    path=Path(path);archive=zipfile.ZipFile(path) if path.suffix.lower()=='.kmz' else None
    docs=[i.filename for i in archive.infolist() if i.filename.lower().endswith('.kml')] if archive else [path.name]
    try:
        for doc in docs:
            with (archive.open(doc) if archive else path.open('rb')) as f:
                nodes=[];folders=[]
                for event,node in ET.iterparse(f,events=('start','end')):
                    tag=_tag(node)
                    if event=='start':
                        nodes.append(node)
                        if tag in ('Document','Folder'):folders.append('')
                        continue
                    if cancelled():raise InterruptedError('KML import cancelled.')
                    parent=nodes[-2] if len(nodes)>1 else None
                    if tag=='name' and parent is not None and _tag(parent) in ('Document','Folder'):folders[-1]=node.text or ''
                    if tag=='Placemark':
                        layer=doc+'::'+('/'.join(x for x in folders if x) or 'Placemarks')
                        if names_only:yield layer,None,None
                        else:
                            props={'Name':_child_text(node,'name'),'Description':_child_text(node,'description'),'kml_id':node.attrib.get('id','')}
                            for element in node.iter():
                                t=_tag(element)
                                if t=='Data' and element.attrib.get('name'):props[element.attrib['name']]=_child_text(element,'value')
                                elif t=='SimpleData' and element.attrib.get('name'):props[element.attrib['name']]=element.text or ''
                            geom=next((g for child in node if (g:=_kml_geometry(child)) is not None),None)
                            if geom is None:raise ValueError('KML placemark lacks a supported local vector geometry.')
                            yield layer,props,geom
                        if parent is not None:parent.remove(node)
                        node.clear()
                    if tag in ('Document','Folder'):
                        folders.pop()
                        if parent is not None:parent.remove(node)
                        node.clear()
                    nodes.pop()
    finally:
        if archive:archive.close()


def source_layers(path):
    if Path(path).suffix.lower() in ('.kml','.kmz'):
        names=set()
        for name,_,_ in kml_records(path,names_only=True):names.add(name)
        if not names:raise ValueError('No local KML vector placemarks; network-linked imagery is not imported.')
        return [{'name':n,'geometry_type':'Local vector placemarks'} for n in sorted(names)]
    if Path(path).suffix.lower() in ('.json','.geojson'):
        import ijson
        with Path(path).open('rb') as f:
            head=f.read(128).lstrip()
        if head.startswith(b'['):return [{'name':'JSON records','geometry_type':None}]
        with Path(path).open('rb') as f:
            for prefix,event,value in ijson.parse(f):
                if prefix=='records' and event=='start_array':return [{'name':'JSON records','geometry_type':None}]
                if prefix=='features' and event=='start_array':return [{'name':Path(path).stem,'geometry_type':'Vector features'}]
    from gis_io import list_layers
    return list_layers(path)


def stream_source(path,layer=None,crs_override=None,batch_size=2000,cancelled=lambda:False,nrows=None):
    path=Path(path);suffix=path.suffix.lower();count=0
    if str(path).lower().endswith(('.csv','.csv.gz')):
        for frame in csv_chunks(path,batch_size,nrows,cancelled):
            if cancelled():raise InterruptedError('Import cancelled.')
            geometry=None;crs=crs_override
            for column in ('geometry','wkt','WKT','geom'):
                if column not in frame:continue
                values=frame[column].str.strip()
                if not values[values.ne('')].str.match(r'^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(',case=False).all():continue
                if not values.ne('').any():continue
                crs=crs_override or ('EPSG:4326' if {'latitude','longitude','confidence'}.issubset(frame.columns) or 'full_plus_code' in frame else None)
                geometry,crs=_reproject(from_wkt(np.array([v or None for v in values],dtype=object)),crs)
                break
            yield Dataset(frame,geometry,{'driver':'Streaming CSV','source':str(path),'layer':'CSV table','source_crs':crs})
        return
    if suffix in ('.kml','.kmz'):
        if crs_override and crs_override.upper() not in ('EPSG:4326','4326'):raise ValueError('KML geometry is WGS84; do not assign a projected CRS.')
        if layer is None:
            names=source_layers(path)
            if len(names)!=1:raise ValueError('Select the KML layer in Source layers / CRS.')
            layer=names[0]['name']
        rows=[];geoms=[]
        for name,props,geom in kml_records(path,cancelled):
            if name!=layer:continue
            rows.append(props);geoms.append(geom);count+=1
            if len(rows)>=batch_size:
                yield Dataset(pd.DataFrame(rows).fillna(''),np.array(geoms,dtype=object),{'driver':'Streaming KML','source':str(path),'layer':layer,'source_crs':'EPSG:4326'});rows=[];geoms=[]
            if nrows is not None and count>=nrows:break
        if rows:yield Dataset(pd.DataFrame(rows).fillna(''),np.array(geoms,dtype=object),{'driver':'Streaming KML','source':str(path),'layer':layer,'source_crs':'EPSG:4326'})
        return
    if suffix in ('.json','.geojson'):
        import ijson
        from building_stream import esri_polygon
        from shapely.geometry import shape,Point,LineString
        with path.open('rb') as f:array=f.read(128).lstrip().startswith(b'[')
        prefix='item' if array else 'features.item';source_crs=crs_override;table=array;esri=False
        if not array:
            with path.open('rb') as f:
                for key,event,value in ijson.parse(f):
                    if key=='records' and event=='start_array':prefix='records.item';table=True;break
                    if key=='geometryType' and event=='string':esri=value.startswith('esri')
                    if key in ('spatialReference.wkid','spatialReference.latestWkid') and event=='number' and not crs_override:source_crs=('ESRI:' if int(value)>100000 else 'EPSG:')+str(int(value))
                    if key in ('spatialReference.wkt','crs.properties.name') and event=='string' and not crs_override:source_crs=value
                    if key=='features' and event=='start_array':break
        rows=[];geoms=[]
        def chunk():
            frame=pd.DataFrame(rows).fillna('');geom=None;actual=source_crs
            if not table:geom,actual=_reproject(np.array(geoms,dtype=object),source_crs or (None if esri else 'EPSG:4326'))
            return Dataset(frame,geom,{'driver':'Streaming JSON','source':str(path),'layer':layer or path.stem,'source_crs':actual})
        with path.open('rb') as f:
            for item in ijson.items(f,prefix,use_float=True):
                if cancelled():raise InterruptedError('JSON import cancelled.')
                count+=1
                if table:rows.append(item)
                else:
                    properties=dict(item.get('attributes' if esri else 'properties') or {});properties.setdefault('__source_fid',str(item.get('id',count)));rows.append(properties);geo=item.get('geometry');geom=None
                    if geo:
                        if 'rings' in geo:geom=esri_polygon(geo)
                        elif 'x' in geo:geom=Point(geo['x'],geo['y'])
                        elif 'paths' in geo and len(geo['paths'])==1:geom=LineString(geo['paths'][0])
                        else:geom=shape(geo)
                    geoms.append(geom)
                if len(rows)>=batch_size:yield chunk();rows=[];geoms=[]
                if nrows is not None and count>=nrows:break
        if rows:yield chunk()
        return
    yield from iter_source(path,layer=layer,crs_override=crs_override,chunk_size=batch_size,nrows=nrows,cancelled=cancelled)
