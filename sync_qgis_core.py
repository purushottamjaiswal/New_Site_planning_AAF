"""Build the QGIS worker from the canonical standalone engine; prevent engine drift."""
from pathlib import Path
import argparse,shutil,hashlib,json

def sync(plugin):
    source=Path(__file__).resolve().parent;target=Path(plugin)/'core';target.mkdir(parents=True,exist_ok=True)
    names=[]
    for p in source.glob('*.py'):
        if p.name in ('app.py','ui_layout.py','sync_qgis_core.py'):continue
        shutil.copy2(p,target/p.name);names.append(p.name)
    for p in target.glob('*.py'):
        if p.name not in names:p.unlink()
    from planner import Rules,ALIASES,REQUIRED
    (Path(plugin)/'schema.json').write_text(json.dumps({'defaults':vars(Rules()),'aliases':ALIASES,'required':REQUIRED},indent=2))
    shutil.copy2(source/'requirements.txt',Path(plugin)/'requirements.txt')
    manifest={'engine_version':'7.0','files':{name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in sorted(names)}}
    (Path(plugin)/'core_manifest.json').write_text(json.dumps(manifest,indent=2));return manifest
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plugin',required=True);args=parser.parse_args();print(json.dumps(sync(args.plugin),indent=2))
