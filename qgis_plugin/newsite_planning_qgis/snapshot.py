"""Main-thread QGIS snapshots. Only ordinary files cross into the worker."""
from pathlib import Path
import csv,json
from qgis.core import (QgsGeometry,QgsCoordinateReferenceSystem,QgsCoordinateTransform,
                       QgsWkbTypes,QgsDistanceArea,NULL,QgsFeatureRequest,QgsExpression)


def text(value):
    if value is None or value==NULL:return ''
    if hasattr(value,'toString'):return value.toString('yyyy-MM-ddTHH:mm:ss')
    return str(value)


def snapshot_layer(layer,kind,path,mapping,project,rules,selected_grid=False,geometry_coords=True,progress=None):
    fields=[f.name() for f in layer.fields()];mapping=dict(mapping)
    has_geometry=layer.isSpatial();is_polygon=QgsWkbTypes.geometryType(layer.wkbType())==QgsWkbTypes.PolygonGeometry
    needs_coords=kind not in ('population',) and geometry_coords and has_geometry
    if has_geometry and (needs_coords or is_polygon) and not layer.crs().isValid():raise ValueError('Source CRS is missing: '+layer.name())
    if needs_coords and kind not in ('grid','buildings','no_build_polygons','land_polygons','water_polygons','rf_coverage','road_lines','backhaul_lines','power_lines') and QgsWkbTypes.geometryType(layer.wkbType())!=QgsWkbTypes.PointGeometry:
        raise ValueError(kind+': use point geometry for inventory/candidate/demand/reference layers.')
    wgs=QgsCoordinateReferenceSystem('EPSG:4326')
    transform=QgsCoordinateTransform(layer.crs(),wgs,project) if has_geometry else None
    area=QgsDistanceArea();area.setSourceCrs(wgs,project.transformContext());area.setEllipsoid('WGS84')
    if selected_grid and kind=='grid':
        if not layer.selectedFeatureCount():raise ValueError('Select at least one grid feature, or disable selected-grid-only.')
        iterator=layer.getSelectedFeatures();total=layer.selectedFeatureCount()
    else:
        conditions=[]
        if mapping.get('country') in fields:
            conditions.append('upper(trim('+QgsExpression.quotedColumnRef(mapping['country'])+'))='+QgsExpression.quotedString(rules['country'].upper()))
        if kind=='grid':
            size=QgsExpression.quotedColumnRef(mapping['size']);period=QgsExpression.quotedColumnRef(mapping['period'])
            conditions.extend(["lower(replace("+size+", ' ', ''))="+QgsExpression.quotedString(rules['grid_size']), 'trim('+period+')='+QgsExpression.quotedString(rules['grid_period'])])
        elif kind=='airtel':conditions.append('trim('+QgsExpression.quotedColumnRef(mapping['period'])+')='+QgsExpression.quotedString(rules['airtel_period']))
        request=QgsFeatureRequest()
        if conditions:request.setFilterExpression(' AND '.join(conditions))
        iterator=layer.getFeatures(request);total=-1 if conditions else layer.featureCount()
    geometries=[];count=0
    if kind in ('buildings','no_build_polygons','land_polygons','water_polygons','rf_coverage','road_lines','backhaul_lines','power_lines'):
        linear=kind in ('road_lines','backhaul_lines','power_lines')
        if not linear and not is_polygon:raise ValueError(kind+': select a polygon/multipolygon layer.')
        with Path(path).open('w',encoding='utf-8') as out:
            out.write('{"type":"FeatureCollection","features":[');first=True
            for feature in iterator:
                geom=QgsGeometry(feature.geometry())
                if geom.isEmpty():raise ValueError('Empty geometry in '+layer.name())
                geom.transform(transform);props={name:text(value) for name,value in zip(fields,feature.attributes())};props.setdefault('building_id',str(feature.id()));props.setdefault('source',layer.name())
                if not first:out.write(',')
                out.write(json.dumps({'type':'Feature','geometry':json.loads(geom.asJson(10)),'properties':props}));first=False;count+=1
                if progress and count%1000==0:progress(count,total)
            out.write(']}')
        if progress:progress(count,total)
        return str(path),mapping,None,{'name':layer.name(),'layer_id':layer.id(),'provider':layer.providerType(),'source_crs':layer.crs().authid(),'rows':count}
    derived=[]
    if needs_coords:
        derived=['__ns_lat','__ns_lon'];mapping.update(lat='__ns_lat',lon='__ns_lon')
    if kind=='grid' and is_polygon:
        derived.append('__ns_area_km2');mapping['area']='__ns_area_km2'
    output_fields=fields+[f for f in derived+['__qgis_fid'] if f not in fields]
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=output_fields);writer.writeheader()
        for feature in iterator:
            values={name:text(value) for name,value in zip(fields,feature.attributes())};values['__qgis_fid']=str(feature.id())
            geom=None
            if needs_coords or (kind=='grid' and is_polygon):
                geom=QgsGeometry(feature.geometry())
                if geom.isEmpty():raise ValueError(f'Empty geometry in {layer.name()}, feature {feature.id()}.')
                geom.transform(transform)
                if needs_coords:
                    if not is_polygon and geom.isMultipart():raise ValueError('Multipart point input must be split into single points: '+layer.name())
                    point=geom.centroid().asPoint() if is_polygon else geom.asPoint()
                    values['__ns_lat']=format(point.y(),'.10f');values['__ns_lon']=format(point.x(),'.10f')
                if kind=='grid' and is_polygon:
                    values['__ns_area_km2']=format(area.measureArea(geom)/1e6,'.12f')
                    country=values.get(mapping['country'],'').strip().upper();gid=values.get(mapping['id'],'').strip()
                    size=values.get(mapping['size'],'').lower().replace(' ','');period=values.get(mapping['period'],'')
                    if country==rules['country'].upper() and size==rules['grid_size'] and period==rules['grid_period']:
                        geometries.append({'type':'Feature','geometry':json.loads(geom.asJson(10)),
                           'properties':{'opco':country,'grid_id':gid}})
            writer.writerow(values);count+=1
            if progress and count%1000==0:progress(count,total)
    if progress:progress(count,total)
    boundary=None
    if kind=='grid' and is_polygon:
        boundary=str(Path(path).with_suffix('.boundaries.geojson'))
        Path(boundary).write_text(json.dumps({'type':'FeatureCollection','features':geometries}),encoding='utf-8')
    return str(path),mapping,boundary,{'name':layer.name(),'layer_id':layer.id(),'provider':layer.providerType(),'source_crs':layer.crs().authid(),'rows':count,'selected_grid_only':bool(selected_grid and kind=='grid')}
