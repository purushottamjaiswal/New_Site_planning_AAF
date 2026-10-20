"""Create or repair the standalone planner's local environment. No QGIS dependency."""
from pathlib import Path
import os,subprocess,sys,venv

def main():
    if sys.version_info<(3,10):raise RuntimeError('Use Python 3.10 or newer for the desktop planner.')
    root=Path(__file__).resolve().parent
    target=root/'.venv';python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('Creating desktop planning environment:',target,flush=True)
        venv.EnvBuilder(with_pip=True).create(target)
    print('Installing complete desktop requirements, including rasterio...',flush=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(root/'requirements.txt')],check=True)
    subprocess.run([str(python),str(root/'check_desktop_environment.py')],check=True)
    print('Desktop environment ready. Start with Start_Planner.bat',flush=True)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('Desktop setup failed:',exc,file=sys.stderr);sys.exit(1)
