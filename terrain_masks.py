"""Disk-indexed exact land/water polygon checks. Never infer land from map tiles."""
import json
from shapely.geometry import Point,Polygon
from pyproj import Geod

_GEOD=Geod(ellps='WGS84')

def nominal_grid_polygon(lat,lon,side_m):
    """Nominal square around a CSV centre, without pretending to restore source topology."""
    x,y,_=_GEOD.fwd([lon]*4,[lat]*4,[225,135,45,315],[side_m/2**.5]*4)
    return Polygon(zip(x,y))

def polygon_hit(session,table,lon,lat,interior=False):
    from shapely.geometry import shape
    p=Point(lon,lat)
    cursor=session.db.execute(f'SELECT t.geometry FROM {table}_rt b JOIN {table} t ON b.rid=t.rid WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(lon,lon,lat,lat))
    for geometry, in cursor:
        session.check();g=shape(json.loads(geometry))
        if (g.contains(p) if interior else g.covers(p)):return True
    return False

def screen_water(session,row):
    """Water wins even when a broad country land outline also covers an inland lake."""
    if polygon_hit(session,'water',row['lon'],row['lat']):return 'Inside/on water polygon (river, lake, lagoon or sea)'
    if not polygon_hit(session,'land',row['lon'],row['lat'],interior=True):return 'Outside/on verified land boundary (ocean or unverified land)'
    return ''
