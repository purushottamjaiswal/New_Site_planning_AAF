"""Run with standard Python 3.10+, outside the QGIS Python console."""
from pathlib import Path
import os,sys,subprocess,venv


def main():
    if sys.version_info<(3,10):raise RuntimeError('Use Python 3.10 or newer; 3.12/3.13 is recommended.')
    base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'NewSitePlanningQGIS'
    target=base/'venv';python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('Creating dedicated planning environment:',target,flush=True)
        venv.EnvBuilder(with_pip=True).create(target)
    clean=os.environ.copy()
    for name in ['PYTHONHOME','PYTHONPATH','PROJ_LIB','PROJ_DATA','GDAL_DATA','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH']:clean.pop(name,None)
    subprocess.run([str(python),'-m','pip','install','-r',str(Path(__file__).with_name('requirements.txt'))],env=clean,check=True)
    subprocess.run([str(python),str(Path(__file__).with_name('worker.py')),'--check'],env=clean,check=True)
    print('\nIn the QGIS plugin choose this Planning Python:\n'+str(python))
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('Setup failed:',exc);sys.exit(1)
