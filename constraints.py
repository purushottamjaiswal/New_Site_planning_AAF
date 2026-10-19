"""Exact topology checks against imported polygon geometries (WGS84)."""
from pathlib import Path
import json
import numpy as np
from shapely.geometry import shape
from shapely import points,covers
from shapely.strtree import STRtree

class PolygonConstraints:
    def __init__(self,grid_path=None,no_build_path=None,country=None,grid_geometries=None,no_build_geometries=None):
        self.grids={};self.grid_features=[];self.exclusions=[]
        if grid_path:
            obj=json.loads(Path(grid_path).read_text(encoding='utf-8'))
            for f in obj.get('features',[]):
                p=f.get('properties',{});gid=str(p.get('grid_id',''));opco=str(p.get('opco','')).upper()
                if country and opco and opco!=country.upper():continue
                geom=self.geometry(f)
                if not gid:raise ValueError('Grid boundary has no mapped grid_id.')
                if gid in self.grids:raise ValueError('Duplicate grid boundary ID: '+gid)
                self.grids[gid]=geom;self.grid_features.append(f)
            if not self.grids:raise ValueError('No grid boundaries match the selected country.')
        if no_build_path:
            obj=json.loads(Path(no_build_path).read_text(encoding='utf-8'))
            self.exclusions=[self.geometry(f) for f in obj.get('features',[])]
            if not self.exclusions:raise ValueError('Selected no-build polygon layer is empty.')
        if grid_geometries is not None:
            for gid,geom in grid_geometries.items():
                from shapely.geometry import mapping
                f={'type':'Feature','geometry':mapping(geom),'properties':{'grid_id':gid,'opco':country or ''}}
                self.geometry(f);self.grids[gid]=geom;self.grid_features.append(f)
        if no_build_geometries is not None:
            from shapely.geometry import mapping
            for geom in no_build_geometries:
                self.exclusions.append(self.geometry({'geometry':mapping(geom)}))
        self.tree=STRtree(self.exclusions) if self.exclusions else None

    @staticmethod
    def geometry(f):
        geom=shape(f['geometry'])
        if geom.geom_type not in ('Polygon','MultiPolygon') or geom.is_empty or not geom.is_valid:
            raise ValueError('Invalid/non-polygon constraint geometry. Repair the source geometry and retry.')
        w,s,e,n=geom.bounds
        if not(-180<=w<=e<=180 and -90<=s<=n<=90):raise ValueError('Polygon constraint is not WGS84.')
        return geom

    def annotate(self,cand):
        coords=points(cand.lon.to_numpy(float),cand.lat.to_numpy(float))
        cand['polygon_constraint_check']='No polygon layer supplied'
        if self.grids:
            for gid,indices in cand.groupby('grid_id').groups.items():
                ids=np.asarray(list(indices),dtype=int)
                if gid not in self.grids:
                    cand.loc[ids,'rejection']='No actual boundary for parent grid';continue
                inside=covers(self.grids[gid],coords[ids])
                cand.loc[ids,'polygon_constraint_check']='Actual parent grid boundary checked'
                cand.loc[ids[~inside],'rejection']='Outside actual parent grid polygon'
        if self.tree is not None and len(cand):
            matches=self.tree.query(coords,predicate='intersects')
            if matches.size:cand.loc[np.unique(matches[0]),'rejection']='Inside/on no-build polygon'
