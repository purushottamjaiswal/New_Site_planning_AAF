"""External Python entry point. No PyQGIS imports; JSON lines on stdout."""
from pathlib import Path
import argparse,json,sys,traceback
sys.path.insert(0,str(Path(__file__).resolve().parent/'core'))


def emit(event,**kwargs):print(json.dumps({'event':event,**kwargs},ensure_ascii=True),flush=True)


def dependency_check():
    import hashlib
    manifest=json.loads(Path(__file__).with_name('core_manifest.json').read_text())
    for name,digest in manifest['files'].items():
        if hashlib.sha256((Path(__file__).parent/'core'/name).read_bytes()).hexdigest()!=digest:raise ValueError('QGIS engine package is inconsistent. Reinstall the matching v7 bundle.')
    import importlib,importlib.metadata,re
    minimum={'numpy':(1,26),'pandas':(2,2),'scipy':(1,12),'shapely':(2,0),'pyproj':(3,6),'pyogrio':(0,11),'ijson':(3,3),'rasterio':(1,3)}
    result={};problems=[]
    for name,version in minimum.items():
        try:
            module=importlib.import_module(name);v=importlib.metadata.version(name)
            result[name]=v;digits=tuple(int(p) for p in re.findall(r'\d+',v)[:2])
            maximum={'numpy':3,'pandas':4,'scipy':2,'shapely':3,'pyproj':4,'pyogrio':1,'ijson':4,'rasterio':2}[name]
            if digits[0]>=maximum:problems.append(f'{name} {v}: unsupported major version; use requirements.txt')
            if digits<version:problems.append(f'{name} {v}: need at least {version[0]}.{version[1]}')
        except Exception as exc:problems.append(f'{name}: {exc}')
    if problems:raise RuntimeError('Planning environment not ready. '+ '; '.join(problems)+'. Run Setup_Planning_Environment.bat outside QGIS, then select its python.exe.')
    emit('dependencies',python=sys.executable,versions=result)


def spatial_outputs(plan,folder,paths):
    exported={'grids':str(folder/'grid_assessment_spatial.geojson'),'sites':str(folder/'proposed_sites.geojson')}
    for key,table in [('airtel','airtel'),('competitors','competitors'),('demand_points','demand')]:
        target=folder/(key+'_spatial.geojson')
        with target.open('w',encoding='utf-8') as f:
            f.write('{"type":"FeatureCollection","features":[');first=True
            for _,row,_ in plan.session.records(table):
                if not first:f.write(',')
                f.write(json.dumps({'type':'Feature','geometry':{'type':'Point','coordinates':[row['lon'],row['lat']]},'properties':row}));first=False
            f.write(']}')
        exported[key]=str(target)
    for role,name in [('no_build_polygons','no_build_polygons_used.geojson'),('buildings','building_footprints_used.geojson')]:
        if (folder/name).exists():exported[role]=str(folder/name)
    return exported


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');parser.add_argument('--job')
    args=parser.parse_args()
    try:
        if args.check:dependency_check();return 0
        if not args.job:raise ValueError('Supply --job JSON path or --check.')
        config=json.loads(Path(args.job).read_text(encoding='utf-8'))
        dependency_check()
        if config.get('operation')=='fetch_osm':
            from engine.buildings import fetch_osm
            n=fetch_osm(config['latitude'],config['longitude'],config['radius_km'],config['target'])
            emit('fetch_result',path=config['target'],count=n);return 0
        from planner import Rules,run_paths,save_plan
        rules=Rules(**config['rules']);cancel_path=Path(args.job).with_name('cancel')
        plan=run_paths(config['paths'],rules,config.get('mappings'),lambda message:emit('log',message=message),cancel_path.exists)
        if cancel_path.exists():raise InterruptedError('Planning cancelled before exporting.')
        try:
            folder=save_plan(plan,config['output_dir'],config['paths'])
            exported=spatial_outputs(plan,folder,config['paths'])
        finally:plan.close()
        provenance=config.get('layer_provenance',{})
        (folder/'qgis_layer_provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
        emit('result',folder=str(folder.resolve()),layers=exported,summary=plan.summary)
        return 0
    except Exception as exc:
        emit('error',message=str(exc),traceback=traceback.format_exc());return 1

if __name__=='__main__':sys.exit(main())
