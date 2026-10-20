"""Explicit synthetic land/water evidence for existing small planning fixtures."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from planner import run_paths as _run_paths

def masks(root,land=None,water=None):
    from shapely.geometry import box,mapping
    result={}
    for name,geom in [('land_polygons',land if land is not None else box(-179,-80,179,80)),('water_polygons',water if water is not None else box(-160,-70,-159,-69))]:
        p=Path(root)/(name+'.geojson');p.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{'source':'Synthetic unit-test mask'},'geometry':mapping(geom)}]}),encoding='utf-8');result[name]=str(p)
    return result

def run_paths(paths,*args,**kwargs):
    with TemporaryDirectory() as td:
        return _run_paths({**masks(td),**paths},*args,**kwargs)
