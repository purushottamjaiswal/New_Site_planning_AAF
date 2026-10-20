"""Bounded GeoPackage export for GIS review; working inputs stay temporary."""
import json
import numpy as np
from shapely.geometry import shape,Point
from pyogrio.raw import write

def export_results(session,path,batch_size=1000):
    for table in ('sites','grids','airtel','competitors'):
        types={key:set(kinds.split(','))-{'null'} for key,kinds in session.db.execute(f'SELECT j.key,group_concat(DISTINCT j.type) FROM {table}, json_each({table}.payload) j GROUP BY j.key ORDER BY j.key')}
        schema=list(types)
        if not schema:continue
        batch=[];geometries=[];append=False
        def flush():
            nonlocal append
            arrays=[]
            for key in schema:
                if types[key] and types[key]<={'integer','real','true','false'}:arrays.append(np.array([float(row[key]) if row.get(key) is not None else np.nan for row in batch],dtype='float64'))
                else:arrays.append(np.array([None if row.get(key) is None else json.dumps(row[key]) if isinstance(row[key],(dict,list)) else str(row[key]) for row in batch],dtype=object))
            write(str(path),geometry=np.array(geometries,dtype=object),field_data=arrays,fields=schema,layer=table,driver='GPKG',geometry_type='Unknown',crs='EPSG:4326',append=append,layer_options={'SPATIAL_INDEX':'YES'});append=True
        for _,row,geometry in session.records(table):
            batch.append(row);geometries.append((shape(json.loads(geometry)) if geometry else Point(row['lon'],row['lat'])).wkb)
            if len(batch)>=batch_size:flush();batch=[];geometries=[]
        if batch:flush()
