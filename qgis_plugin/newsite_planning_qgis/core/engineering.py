"""Optional line proximity, DEM slope screening and imported validated RF footprints.
No imagery extraction or uncalibrated propagation claim is made.
"""
import json,math
from pathlib import Path
from shapely.geometry import Point,shape
from shapely.ops import transform
from pyproj import Transformer,Geod
from session_db import distance

class Engineering:
    def __init__(self,session,rules,terrain=None):
        self.s=session;self.r=rules;self.dem=None;self.has_rf=bool(session.count('rf_coverage'));self.network_kinds={x[0] for x in session.db.execute("SELECT DISTINCT json_extract(payload,'$.kind') FROM network")}
        if rules.rf_required and not session.count('rf_coverage'):raise ValueError('Validated RF coverage footprints are required.')
        if rules.rf_required and rules.location_mode!='Supplied candidates':raise ValueError('Required RF evidence needs Supplied candidates with stable IDs.')
        if terrain:
            try:import rasterio
            except ImportError as exc:raise ValueError('DEM support requires rasterio; reinstall requirements.txt.') from exc
            self.dem=rasterio.open(terrain)
            if not self.dem.crs:self.dem.close();raise ValueError('DEM must declare its actual horizontal CRS.')
            self.to_dem=Transformer.from_crs(4326,self.dem.crs,always_xy=True)
            self.from_dem=Transformer.from_crs(self.dem.crs,4326,always_xy=True)
        if rules.terrain_required and not self.dem:raise ValueError('Terrain screening is required: supply a DEM raster.')
    def close(self):
        if self.dem:self.dem.close()
    def terrain(self,lat,lon):
        if not self.dem:return None,None
        from rasterio.windows import Window
        import numpy as np
        x,y=self.to_dem.transform(lon,lat);row,col=self.dem.index(x,y)
        if not 1<=row<self.dem.height-1 or not 1<=col<self.dem.width-1:return None,None
        data=self.dem.read(1,window=Window(col-1,row-1,3,3),masked=True)
        if data.shape!=(3,3) or np.ma.getmaskarray(data).any() or not np.isfinite(data).all():return None,None
        coords=[self.from_dem.transform(*self.dem.xy(row,col-1)),self.from_dem.transform(*self.dem.xy(row,col+1)),self.from_dem.transform(*self.dem.xy(row-1,col)),self.from_dem.transform(*self.dem.xy(row+1,col))]
        geod=Geod(ellps='WGS84');dx=geod.inv(*coords[0],*coords[1])[2];dy=geod.inv(*coords[2],*coords[3])[2]
        slope=math.degrees(math.atan(math.hypot(float(data[1,2]-data[1,0])/max(dx,.001),float(data[2,1]-data[0,1])/max(dy,.001))))
        return float(data[1,1]),slope
    def line_distance(self,kind,lat,lon,limit_km):
        if kind not in self.network_kinds:return None
        radius=max(limit_km,(self.r.road_setback_m+self.r.site_compound_radius_m)/1000 if kind=='road' else 0,.1)
        dy=radius/110;dx=dy/max(.01,math.cos(math.radians(lat)))
        transformer=Transformer.from_crs(4326,f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m',always_xy=True)
        best=None
        for geometry, in self.s.db.execute("SELECT t.geometry FROM network_rt b JOIN network t ON b.rid=t.rid WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? AND json_extract(t.payload,'$.kind')=?",(lon-dx,lon+dx,lat-dy,lat+dy,kind)):
            self.s.check();metres=transform(transformer.transform,shape(json.loads(geometry))).distance(Point(0,0));best=metres if best is None else min(best,metres)
        return None if best is None else best/1000
    def screen(self,row):
        errors=self.compound(row);row['compound_screening']=('Rejected: '+'; '.join(errors)) if errors else 'Passed requested compound/setback checks against supplied geometry' if self.r.site_compound_radius_m or self.r.water_setback_m else 'Not requested: centre-point screening only';row['terrain_elevation_m'],row['terrain_slope_deg']=self.terrain(row['lat'],row['lon'])
        if self.dem or self.r.terrain_required:
            if row['terrain_slope_deg'] is None:errors.append('DEM has no valid 3x3 neighbourhood at this point')
            elif row['terrain_slope_deg']>self.r.max_slope_deg:errors.append('Terrain slope exceeds configured limit')
        for kind in ('road','backhaul','power'):
            limit=getattr(self.r,'max_'+kind+'_km');value=self.line_distance(kind,row['lat'],row['lon'],limit if limit else 10)
            current=row.get('nearest_'+kind+'_km')
            if value is not None:row['nearest_'+kind+'_km']=min(current,value) if current is not None and math.isfinite(current) else value
            actual=row.get('nearest_'+kind+'_km')
            if limit and (actual is None or not math.isfinite(actual) or actual>limit):errors.append(f'Beyond {kind} reference/line proximity limit')
            if kind=='road' and value is not None and value*1000<=self.r.road_setback_m+self.r.site_compound_radius_m and self.r.building_rule!='Rooftop search':errors.append('Inside road-line setback; verify actual road right-of-way')
        evidence=self.s.db.execute("SELECT payload FROM rf_coverage WHERE gid=? LIMIT 1",(row['source_candidate_id'],)).fetchone()
        row['rf_evidence']=('Imported validated footprint (user-declared): '+str(json.loads(evidence[0]).get('calibration_id'))+'; '+str(json.loads(evidence[0]).get('validation_date'))) if evidence else 'Assumed radius only; no validated RF prediction'
        if self.r.rf_required and not evidence:errors.append('No validated RF footprint for this candidate ID')
        return errors
    def compound(self,row):
        radius=self.r.site_compound_radius_m if self.r.building_rule!='Rooftop search' else 0
        water_radius=radius+self.r.water_setback_m
        if water_radius<=0:return []
        lat,lon=row['lat'],row['lon'];local=f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m'
        to_geo=Transformer.from_crs(local,4326,always_xy=True)
        circle=transform(to_geo.transform,Point(0,0).buffer(water_radius));w,south,e,n=circle.bounds;errors=[]
        for geom, in self.s.db.execute('SELECT t.geometry FROM water_rt b JOIN water t ON b.rid=t.rid WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(w,e,south,n)):
            if shape(json.loads(geom)).intersects(circle):errors.append('Compound/water setback intersects a water polygon');break
        if radius:
            plot=transform(to_geo.transform,Point(0,0).buffer(radius));land=False
            for geom, in self.s.db.execute('SELECT t.geometry FROM land_rt b JOIN land t ON b.rid=t.rid WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(w,e,south,n)):
                if shape(json.loads(geom)).covers(plot):land=True;break
            if not land:errors.append('Compound is not contained within one verified land polygon; check split land tiles')
            for geom, in self.s.db.execute('SELECT t.geometry FROM no_build_rt b JOIN no_build t ON b.rid=t.rid WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(w,e,south,n)):
                if shape(json.loads(geom)).intersects(plot):errors.append('Compound intersects a no-build polygon');break
            maximum=self.s.db.execute("SELECT max(json_extract(payload,'$.radius_m')) FROM buffers").fetchone()[0] or 0
            for _,buffer,dist,_ in self.s.spatial('buffers',lat,lon,(maximum+radius)/1000):
                if dist*1000<=buffer['radius_m']+radius:errors.append('Compound intersects a no-build buffer');break
        return errors

    def rf_allows(self,candidate,demand):
        if not self.has_rf:return not self.r.rf_required
        key=candidate['source_candidate_id']
        has=self.s.db.execute('SELECT 1 FROM rf_coverage WHERE gid=? LIMIT 1',(key,)).fetchone()
        if not has:return not self.r.rf_required
        point=Point(demand['lon'],demand['lat'])
        for geometry, in self.s.db.execute('SELECT t.geometry FROM rf_coverage_rt b JOIN rf_coverage t ON b.rid=t.rid WHERE t.gid=? AND b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(key,point.x,point.x,point.y,point.y)):
            if shape(json.loads(geometry)).covers(point):return True
        return False
