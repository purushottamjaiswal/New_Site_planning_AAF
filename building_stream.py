"""Streaming GeoJSON footprints, and GDAL chunk access for other vector data."""
from pathlib import Path
import numpy as np
import pandas as pd
from shapely.geometry import shape,box,Polygon,MultiPolygon
from gis_io import Dataset,iter_source,_reproject


def building_chunks(path,layer=None,crs_override=None,bbox=None,cancelled=lambda:False,chunk_size=2000):
    path=Path(path)
    # GeoJSON feature arrays can be streamed without loading a whole JSON document.
    if path.suffix.lower() in ('.json','.geojson'):
        import ijson
        geojson=False;esri=False;declared=None;source_crs=crs_override or 'EPSG:4326'
        with path.open('rb') as f:
            for prefix,event,value in ijson.parse(f):
                if prefix=='type' and event=='string' and value=='FeatureCollection':geojson=True
                if prefix=='geometryType' and event=='string' and value=='esriGeometryPolygon':esri=True
                if prefix in ('spatialReference.wkid','spatialReference.latestWkid') and event=='number':declared=('ESRI:' if int(value)>100000 else 'EPSG:')+str(int(value))
                if prefix=='spatialReference.wkt' and event=='string':declared=value
                if prefix=='crs.properties.name' and event=='string' and not crs_override:source_crs=value
                if prefix=='features' and event=='start_array':break
        if not(geojson or esri):
            with path.open('rb') as f:first=next(ijson.items(f,'features.item',use_float=True),{})
            geometry=first.get('geometry') or {}
            geojson=geometry.get('type') in ('Polygon','MultiPolygon')
            esri='rings' in geometry
        if geojson or esri:
            if esri:
                if not(crs_override or declared):raise ValueError('Esri JSON requires its spatialReference before features, or an explicit source CRS override.')
                source_crs=crs_override or declared
            rows=[];geometries=[];seen=0
            def chunk():
                geoms,actual_crs=_reproject(np.array(geometries,dtype=object),source_crs)
                frame=pd.DataFrame(rows).fillna('')
                if bbox:
                    mask=np.array([g is not None and not g.is_empty and g.intersects(box(*bbox)) for g in geoms]);frame=frame.loc[mask].reset_index(drop=True);geoms=geoms[mask]
                return Dataset(frame,geoms,{'driver':'Streaming Esri JSON' if esri else 'Streaming GeoJSON','layer':layer or path.stem,'source_crs':actual_crs,'coordinate_crs':'EPSG:4326'})
            with path.open('rb') as f:
                for feature in ijson.items(f,'features.item',use_float=True):
                    if cancelled():raise InterruptedError('GeoJSON building import cancelled.')
                    seen+=1;props=dict(feature.get('attributes' if esri else 'properties') or {});props.setdefault('building_id',str(feature.get('id',seen)))
                    rows.append(props)
                    try:geom=esri_polygon(feature['geometry']) if esri else shape(feature['geometry']) if feature.get('geometry') else None
                    except (ValueError,TypeError,KeyError):geom=None
                    geometries.append(geom)
                    if len(rows)>=chunk_size:yield chunk();rows=[];geometries=[]
            if rows:yield chunk()
            return
    yield from iter_source(path,layer=layer,crs_override=crs_override,bbox=bbox,cancelled=cancelled,chunk_size=chunk_size)


def esri_polygon(geometry):
    # Ring nesting handles shells, holes and disjoint parts independently of winding.
    if not isinstance(geometry,dict):return None
    rings=[Polygon([(p[0],p[1]) for p in ring]) for ring in geometry.get('rings',[])]
    rings=[g for g in rings if not g.is_empty]
    depths=[sum(other.area>g.area and other.covers(g) for other in rings) for g in rings]
    parts=[]
    for i,shell in enumerate(rings):
        if depths[i]%2:continue
        holes=[list(hole.exterior.coords) for j,hole in enumerate(rings) if depths[j]==depths[i]+1 and shell.covers(hole)]
        parts.append(Polygon(shell.exterior.coords,holes))
    return parts[0] if len(parts)==1 else MultiPolygon(parts)


def building_layers(path):
    """Inspect JSON headers without the general table reader's document-size limit."""
    path=Path(path)
    if path.suffix.lower() in ('.json','.geojson'):
        import ijson
        with path.open('rb') as f:
            geojson=False;esri=False
            for prefix,event,value in ijson.parse(f):
                if prefix=='type' and event=='string' and value=='FeatureCollection':geojson=True
                if prefix=='geometryType' and event=='string' and value=='esriGeometryPolygon':esri=True
                if prefix=='features' and event=='start_array':
                    if geojson or esri:return [{'name':path.stem,'geometry_type':'Polygon / MultiPolygon (streamed)'}]
                    break
        with path.open('rb') as f:first=next(ijson.items(f,'features.item',use_float=True),{})
        geometry=first.get('geometry') or {}
        if geometry.get('type') in ('Polygon','MultiPolygon') or 'rings' in geometry:return [{'name':path.stem,'geometry_type':'Polygon / MultiPolygon (streamed)'}]
    from gis_io import list_layers
    return list_layers(path)
