"""Native PyQGIS dock; scientific work executes out of process."""
from pathlib import Path
from datetime import datetime
import csv,json,os,traceback,tempfile,shutil
from qgis.PyQt.QtCore import Qt,QProcess,QProcessEnvironment,QSettings,QUrl,QTimer
from qgis.PyQt.QtGui import QDesktopServices,QColor
from qgis.PyQt.QtWidgets import (QDockWidget,QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QScrollArea,
    QFormLayout,QLineEdit,QPushButton,QLabel,QComboBox,QCheckBox,QSpinBox,QDoubleSpinBox,
    QFileDialog,QMessageBox,QPlainTextEdit,QProgressBar,QGroupBox,QTableWidget,QTableWidgetItem,
    QHeaderView,QProgressDialog,QApplication)
from qgis.core import (QgsProject,QgsMapLayerProxyModel,QgsVectorLayer,QgsRasterLayer,
    QgsMarkerSymbol,QgsFillSymbol,QgsRendererCategory,QgsCategorizedSymbolRenderer,
    QgsWkbTypes,QgsPalLayerSettings,QgsVectorLayerSimpleLabeling,QgsTextFormat,
    QgsGeometry,QgsCoordinateReferenceSystem,QgsCoordinateTransform)
from qgis.gui import QgsMapLayerComboBox
from .snapshot import snapshot_layer

ROOT=Path(__file__).resolve().parent
SCHEMA=json.loads((ROOT/'schema.json').read_text(encoding='utf-8'))
CHOICES={'country':['NG','CD','CG','GA','KE','MG','MW','NE','RW','TD','TZ','UG','ZM','SC'],
 'grid_size':['1km','3km'],'mode':['Population','MR preview'],'population_kind':['People per grid','People per km2'],
 'target_policy':['Red + Amber','Red only'],'location_policy':['Current behaviour','Target grids only','Red grids only','Any selected grid'],'priority_policy':['Weighted demand','Red demand first'],'building_storage':['Disk index'],
 'location_mode':['Quick demo (grid centroid)','Settlement search','Area search','Supplied candidates','Grid centroid (legacy)'],
 'building_rule':['Ground clearance','Context only','Rooftop search']}
TITLES={'grid':'KPI grids','airtel':'Airtel inventory / pipeline','competitor':'Competitor inventory',
 'population':'Population per grid','candidates':'Feasible candidate points','demand_points':'Detailed demand points',
 'exclusions':'Circular no-build buffers','infrastructure':'Infrastructure reference points',
 'buildings':'Open building polygons','no_build_polygons':'No-build polygons','land_polygons':'Required land polygons','water_polygons':'Required water polygons','road_lines':'Road lines','backhaul_lines':'Fiber/backhaul lines','power_lines':'Power lines','terrain':'DEM raster (File mode only)','rf_coverage':'Validated RF footprints'}

class PlanningDock(QDockWidget):
    def __init__(self,iface):
        super().__init__('New Site Planning',iface.mainWindow());self.setObjectName('NewSitePlanningDock')
        self.iface=iface;self.process=None;self.job=None;self.result=None;self.stdout='';self.error_seen=False;self.server=None;self.map_process=None
        self.settings=QSettings();self.sources={};self.controls={};self.mappings=json.loads(json.dumps(SCHEMA['aliases']))
        self.setMinimumWidth(470);self.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea)
        body=QWidget();self.setWidget(body);layout=QVBoxLayout(body)
        title=QLabel('<b>Telecom site planning</b><br>Shared v7 production core; required land/water screening. Native QGIS GUI validation pending.');title.setWordWrap(True);layout.addWidget(title)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.make_sources();self.make_rules();self.make_mapping();self.make_environment();self.make_results()
        self.progress=QProgressBar();self.progress.setRange(0,100);layout.addWidget(self.progress)
        row=QHBoxLayout();self.run_button=QPushButton('Run planning');self.run_button.clicked.connect(self.run);row.addWidget(self.run_button)
        cancel=QPushButton('Cancel');cancel.clicked.connect(self.cancel);row.addWidget(cancel);layout.addLayout(row)
        self.status=QLabel('Select sources and a planning Python environment.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.restore()

    def scroll_tab(self,title):
        area=QScrollArea();area.setWidgetResizable(True);content=QWidget();area.setWidget(content);self.tabs.addTab(area,title)
        return content,QVBoxLayout(content)

    def make_sources(self):
        content,layout=self.scroll_tab('Sources');self.sources_tab=self.tabs.widget(self.tabs.count()-1)
        hint=QLabel('Use CSV files or existing QGIS layers. Load GPKG/SHP/PostGIS data into QGIS, then choose Project layer. Polygon grid geometry provides real-boundary checks.');hint.setWordWrap(True);layout.addWidget(hint)
        defaults={'grid':'Postgres_Sep_2026(1).csv','airtel':'Sep_26_SM.csv','competitor':'Comp_data.csv'}
        for kind,title in TITLES.items():
            box=QGroupBox(title);outer=QVBoxLayout(box)
            mode=QComboBox();mode.addItems(['File','Project layer']);outer.addWidget(mode)
            row=QHBoxLayout();path=QLineEdit(str(ROOT/'data'/defaults[kind]) if kind in defaults else '');row.addWidget(path)
            browse=QPushButton('Browse');browse.clicked.connect(lambda checked=False,k=kind:self.browse_source(k));row.addWidget(browse);outer.addLayout(row)
            layer=QgsMapLayerComboBox();layer.setAllowEmptyLayer(True);layer.setFilters(QgsMapLayerProxyModel.VectorLayer)
            if kind in ('buildings','no_build_polygons','land_polygons','water_polygons','rf_coverage'):layer.setFilters(QgsMapLayerProxyModel.PolygonLayer)
            layer.setLayer(None);outer.addWidget(layer);layout.addWidget(box)
            self.sources[kind]={'mode':mode,'path':path,'layer':layer}
        self.geometry_coords=QCheckBox('Use layer geometry for WGS84 coordinates');self.geometry_coords.setChecked(True);layout.addWidget(self.geometry_coords)
        self.selected_grids=QCheckBox('Use selected grid features only (project layer)');layout.addWidget(self.selected_grids)
        fetch=QPushButton('Fetch OSM buildings at map centre');fetch.clicked.connect(self.fetch_osm);layout.addWidget(fetch)
        self.osm_radius=QDoubleSpinBox();self.osm_radius.setRange(.1,5);self.osm_radius.setValue(.5);self.osm_radius.setSuffix(' km area radius');layout.addWidget(self.osm_radius)
        osm=QPushButton('Add OpenStreetMap basemap to QGIS');osm.clicked.connect(self.add_osm);layout.addWidget(osm);layout.addStretch()

    def make_rules(self):
        groups=[('Scope / score',['country','grid_size','grid_period','airtel_period','mode','population_kind','max_sites',
                    'target_policy','location_policy','priority_policy','persist_project','export_gpkg','adaptive_import','import_memory_mb','terrain_required','rf_required','max_slope_deg','site_compound_radius_m','water_setback_m','road_setback_m','import_batch_size','service_radius_km','min_density','min_incremental','red_weight','amber_weight','competitor_radius_km','competitor_boost']),
                ('Siting',['location_mode','search_spacing_m','distance_decay','min_airtel_km','min_new_km',
                    'max_candidate_points','reserve_planned','respect_site_present','max_road_km','max_backhaul_km','max_power_km']),
                ('Buildings',['building_storage','building_import_batch_size','building_rule','building_clearance_m','building_context_radius_m','building_density_boost',
                    'building_min_confidence','min_roof_area_m2','building_clip_bbox'])]
        labels={'grid_period':'Grid snapshot','airtel_period':'Airtel snapshot','service_radius_km':'Assumed service radius (km)',
          'min_airtel_km':'Minimum Airtel distance (km)','min_new_km':'Minimum new-site spacing (km)',
          'max_sites':'Maximum proposed sites','mode':'Demand mode','building_clip_bbox':'Clip: south,west,north,east'}
        for title,keys in groups:
            content,layout=self.scroll_tab(title);form=QFormLayout();layout.addLayout(form)
            for key in keys:
                default=SCHEMA['defaults'][key]
                if key in CHOICES:
                    control=QComboBox();control.addItems(CHOICES[key]);control.setCurrentText(str(default))
                elif isinstance(default,bool):control=QCheckBox();control.setChecked(default)
                elif isinstance(default,int):control=QSpinBox();control.setRange(0,2147483647);control.setValue(default)
                elif isinstance(default,float):control=QDoubleSpinBox();control.setRange(0,1e12);control.setDecimals(6);control.setValue(default)
                else:control=QLineEdit(str(default))
                self.controls[key]=control;form.addRow(labels.get(key,key.replace('_',' ').capitalize()),control)
            hint=QLabel('MR preview is not population. A service radius is not a propagation prediction. Building outlines do not certify roof strength or access. Infrastructure limits of 0 are disabled.');hint.setWordWrap(True);layout.addWidget(hint);layout.addStretch()
        self.controls['mode'].setCurrentText('MR preview')

    def make_mapping(self):
        content,layout=self.scroll_tab('Field mapping');self.mapping_tab=self.tabs.widget(self.tabs.count()-1)
        self.mapping_kind=QComboBox();self.mapping_kind.addItems(list(self.mappings));self.mapping_kind.currentTextChanged.connect(self.show_mapping);layout.addWidget(self.mapping_kind)
        inspect=QPushButton('Inspect source columns');inspect.clicked.connect(self.inspect_mapping);layout.addWidget(inspect)
        self.mapping_table=QTableWidget();self.mapping_table.setColumnCount(2);self.mapping_table.setHorizontalHeaderLabels(['Role','Source column'])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);layout.addWidget(self.mapping_table)
        hint=QLabel('Required roles are marked *. Geometry-derived lat/lon and polygon area override their field mappings in snapshots. Other column mappings must match the source exactly.');hint.setWordWrap(True);layout.addWidget(hint)
        self.show_mapping('grid')

    def show_mapping(self,kind,columns=None):
        self.mapping_table.blockSignals(True);mapping=self.mappings[kind];self.mapping_table.setRowCount(len(mapping))
        for row,(key,value) in enumerate(mapping.items()):
            item=QTableWidgetItem(key+(' *' if key in SCHEMA['required'][kind] else ''));item.setFlags(item.flags()&~Qt.ItemIsEditable);self.mapping_table.setItem(row,0,item)
            combo=QComboBox();combo.setEditable(True);combo.addItems(['']+(columns or []));combo.setCurrentText(value)
            combo.currentTextChanged.connect(lambda text,k=kind,f=key:self.mappings[k].__setitem__(f,text));self.mapping_table.setCellWidget(row,1,combo)
        self.mapping_table.blockSignals(False)

    def inspect_mapping(self):
        try:
            kind=self.mapping_kind.currentText();source=self.sources[kind]
            if source['mode'].currentText()=='Project layer':
                layer=source['layer'].currentLayer()
                if layer is None:raise ValueError('Choose a project layer first.')
                cols=[f.name() for f in layer.fields()]
            else:
                path=source['path'].text().strip()
                for encoding in ('utf-8-sig','cp1252'):
                    try:
                        with open(path,encoding=encoding,newline='') as f:cols=next(csv.reader(f))
                        break
                    except UnicodeDecodeError:continue
            self.show_mapping(kind,cols);self.status.setText(f'{kind}: {len(cols)} source fields.')
        except Exception as exc:self.problem(exc)

    def make_environment(self):
        content,layout=self.scroll_tab('Environment');self.environment_tab=self.tabs.widget(self.tabs.count()-1)
        hint=QLabel('Select an external Python with numpy, pandas, scipy, Shapely 2 and pyproj. The plugin never installs packages into QGIS Python. A desktop-app environment may be reused.');hint.setWordWrap(True);layout.addWidget(hint)
        self.python=QLineEdit();layout.addWidget(QLabel('Planning Python executable'));layout.addWidget(self.python)
        browse=QPushButton('Browse python.exe / python3');browse.clicked.connect(self.browse_python);layout.addWidget(browse)
        check=QPushButton('Check planning dependencies');check.clicked.connect(self.check_dependencies);layout.addWidget(check)
        help_button=QPushButton('Environment setup instructions');help_button.clicked.connect(self.setup_help);layout.addWidget(help_button)
        self.output=QLineEdit(str(Path.home()/'Documents'/'NewSitePlanningQGIS'));layout.addWidget(QLabel('Output directory'));layout.addWidget(self.output)
        choose=QPushButton('Choose output directory');choose.clicked.connect(self.choose_output);layout.addWidget(choose)
        save=QPushButton('Save scenario JSON');save.clicked.connect(self.save_scenario);layout.addWidget(save)
        load=QPushButton('Load scenario JSON');load.clicked.connect(self.load_scenario);layout.addWidget(load)
        layout.addStretch()

    def make_results(self):
        content,layout=self.scroll_tab('Results / log');self.results_tab=self.tabs.widget(self.tabs.count()-1)
        self.result_label=QLabel('Results will be added as a new QGIS layer group.');self.result_label.setWordWrap(True);layout.addWidget(self.result_label)
        for title,callback in [('Open result folder',self.open_folder),('Open OSM / official Google viewer',self.open_web),('Open Google Earth KMZ',self.open_earth),('Open plugin guide',self.open_help)]:
            b=QPushButton(title);b.clicked.connect(callback);layout.addWidget(b)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMinimumHeight(250);layout.addWidget(self.log)

    def problem(self,error):
        self.status.setText(str(error));QMessageBox.warning(self,'New Site Planning',str(error))

    def browse_source(self,kind):
        if kind=='no_build_polygons':filter_='Polygon GeoJSON (*.geojson *.json);;All files (*)'
        elif kind=='terrain':filter_='DEM raster (*.tif *.tiff);;All files (*)'
        elif kind in ('buildings','land_polygons','water_polygons','rf_coverage','road_lines','backhaul_lines','power_lines'):filter_='GIS data (*.gpkg *.shp *.geojson *.json *.kml *.kmz *.csv *.csv.gz);;All files (*)'
        else:filter_='CSV (*.csv);;All files (*)'
        path,_=QFileDialog.getOpenFileName(self,'Select '+TITLES[kind],str(Path.home()),filter_)
        if path:self.sources[kind]['path'].setText(path);self.sources[kind]['mode'].setCurrentText('File')

    def browse_python(self):
        path,_=QFileDialog.getOpenFileName(self,'Select external Python executable',str(Path.home()),'Executables (*.exe);;All files (*)')
        if path:self.python.setText(path)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'Planning outputs',self.output.text())
        if path:self.output.setText(path)

    def rules(self):
        result={}
        for key,w in self.controls.items():
            if isinstance(w,QComboBox):result[key]=w.currentText()
            elif isinstance(w,QCheckBox):result[key]=w.isChecked()
            elif isinstance(w,(QSpinBox,QDoubleSpinBox)):result[key]=w.value()
            else:result[key]=w.text().strip()
        result['country']=result['country'].upper();return result

    def config(self):
        sources={}
        for kind,row in self.sources.items():
            layer=row['layer'].currentLayer();sources[kind]={'mode':row['mode'].currentText(),'path':row['path'].text(),
                'layer_id':layer.id() if layer is not None else ''}
        return {'sources':sources,'rules':self.rules(),'mappings':self.mappings,'python':self.python.text(),
                'output_dir':self.output.text(),'geometry_coordinates':self.geometry_coords.isChecked(),
                'selected_grids_only':self.selected_grids.isChecked()}

    def apply_config(self,config):
        for kind,data in config.get('sources',{}).items():
            if kind in self.sources:
                self.sources[kind]['mode'].setCurrentText(data.get('mode','File'));self.sources[kind]['path'].setText(data.get('path',''))
                self.sources[kind]['layer'].setLayer(QgsProject.instance().mapLayer(data.get('layer_id','')))
        for key,value in config.get('rules',{}).items():
            if key not in self.controls:continue
            w=self.controls[key]
            if isinstance(w,QComboBox):w.setCurrentText(str(value))
            elif isinstance(w,QCheckBox):w.setChecked(bool(value))
            elif isinstance(w,(QSpinBox,QDoubleSpinBox)):w.setValue(value)
            else:w.setText(str(value))
        for kind,mapping in config.get('mappings',{}).items():
            if kind in self.mappings:self.mappings[kind].update(mapping)
        self.show_mapping(self.mapping_kind.currentText());self.python.setText(config.get('python',self.python.text()));self.output.setText(config.get('output_dir',self.output.text()))
        self.geometry_coords.setChecked(config.get('geometry_coordinates',True));self.selected_grids.setChecked(config.get('selected_grids_only',False))

    def persist(self):self.settings.setValue('NewSitePlanningQGIS/config',json.dumps(self.config()))
    def restore(self):
        value=self.settings.value('NewSitePlanningQGIS/config','')
        if value:
            try:self.apply_config(json.loads(value))
            except Exception:pass
        if not self.python.text():
            base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'NewSitePlanningQGIS'/'venv'
            exe=base/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            if exe.exists():self.python.setText(str(exe))

    def save_scenario(self):
        path,_=QFileDialog.getSaveFileName(self,'Save scenario','','Scenario JSON (*.json)')
        if path:Path(path).write_text(json.dumps(self.config(),indent=2),encoding='utf-8')
    def load_scenario(self):
        path,_=QFileDialog.getOpenFileName(self,'Load scenario','','Scenario JSON (*.json)')
        if path:
            try:self.apply_config(json.loads(Path(path).read_text(encoding='utf-8')))
            except Exception as exc:self.problem(exc)

    def setup_help(self):
        QMessageBox.information(self,'Dedicated planning environment',
          'Outside QGIS, run the bundled Setup_Planning_Environment.bat (Windows) or python3 setup_environment.py.\n\nIt creates a separate environment, installs requirements, checks them, and prints the Python path to select here.\n\nPlugin folder:\n'+str(ROOT)+'\n\nDo not install packages into QGIS Python. Your IT-approved package mirror can be used.')

    def busy(self):return self.process is not None and self.process.state()!=QProcess.NotRunning
    def set_busy(self,value):
        self.run_button.setEnabled(not value);self.progress.setRange(0,0 if value else 100)
        for i in range(self.tabs.count()):self.tabs.setTabEnabled(i,not value or self.tabs.widget(i) is self.results_tab)
        if value:self.tabs.setCurrentWidget(self.results_tab)

    def executable(self):
        path=Path(self.python.text().strip())
        if not path.is_file():raise ValueError('Choose a real external Python executable under Environment. Do not choose qgis-bin.exe.')
        if path.name.lower().startswith(('qgis','pythonw')):raise ValueError('Select console python.exe, not qgis-bin.exe or pythonw.exe.')
        return str(path.resolve())

    def launch(self,args):
        if self.busy():raise ValueError('A planning operation is already running.')
        exe=self.executable();self.persist();self.stdout='';self.error_seen=False
        if self.process is not None:self.process.deleteLater()
        self.process=QProcess(self);env=QProcessEnvironment.systemEnvironment()
        for name in ['PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','PROJ_LIB','PROJ_DATA','GDAL_DATA']:env.remove(name)
        env.insert('PYTHONNOUSERSITE','1');env.insert('PYTHONIOENCODING','utf-8');self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT));self.process.readyReadStandardOutput.connect(self.read_stdout);self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.finished);self.process.errorOccurred.connect(self.process_error)
        self.set_busy(True);self.process.start(exe,['-u',str(ROOT/'worker.py')]+args)

    def check_dependencies(self):
        try:self.launch(['--check'])
        except Exception as exc:self.problem(exc)

    def export_progress(self,dialog,count,total):
        dialog.setMaximum(total if total>0 else 0);dialog.setValue(count);QApplication.processEvents()
        if dialog.wasCanceled():raise InterruptedError('Source snapshot cancelled.')

    def run(self):
        if self.busy():return
        dialog=None
        try:
            self.executable();rules=self.rules();base=Path(self.output.text().strip()).expanduser().resolve()
            if not self.output.text().strip():raise ValueError('Choose an output directory.')
            job=Path(tempfile.mkdtemp(prefix='aaf_qgis_job_'));self.job=job/'job.json'
            paths={};maps=json.loads(json.dumps(self.mappings));provenance={}
            dialog=QProgressDialog('Snapshotting QGIS sources...','Cancel',0,1,self);dialog.setWindowModality(Qt.WindowModal);dialog.setMinimumDuration(0);dialog.setAutoClose(False);dialog.setAutoReset(False)
            for kind,row in self.sources.items():
                if row['mode'].currentText()=='File':
                    path=row['path'].text().strip()
                    if path:
                        if not Path(path).exists():raise ValueError('File/GDB folder not found for '+kind+': '+path)
                        if kind=='no_build_polygons' and Path(path).suffix.lower() not in ('.geojson','.json'):raise ValueError('No-build polygons require GeoJSON or a project polygon layer; use Circular no-build buffers for CSV input.')
                        paths[kind]=str(Path(path).expanduser().resolve())
                    continue
                if kind=='terrain':raise ValueError('Select the DEM TIFF using File mode.')
                layer=row['layer'].currentLayer()
                if layer is None or not layer.isValid():raise ValueError('Choose a valid project layer for '+kind+'.')
                mapping=maps.get(kind,{})
                if kind in SCHEMA['required']:
                    names={f.name() for f in layer.fields()}
                    for role in SCHEMA['required'][kind]:
                        if role in ('lat','lon') and self.geometry_coords.isChecked() and layer.isSpatial():continue
                        if mapping.get(role) not in names:raise ValueError(f'{kind}: map {role} to a source field under Field mapping.')
                suffix='.geojson' if kind in ('buildings','no_build_polygons','land_polygons','water_polygons','rf_coverage','road_lines','backhaul_lines','power_lines') else '.csv';target=job/(kind+suffix)
                dialog.setLabelText('Snapshotting '+layer.name())
                path,mapping,boundary,info=snapshot_layer(layer,kind,target,mapping,QgsProject.instance(),rules,self.selected_grids.isChecked(),self.geometry_coords.isChecked(),lambda c,t:self.export_progress(dialog,c,t))
                paths[kind]=path;provenance[kind]=info
                if kind in maps:maps[kind]=mapping
                if boundary:paths['grid_boundaries']=boundary
            for kind in ('grid','airtel','competitor','land_polygons','water_polygons'):
                if kind not in paths:raise ValueError('Select '+kind+' source.')
            if rules['mode']=='Population' and 'population' not in paths:raise ValueError('Population mode requires actual population input. MR preview is available for current files.')
            if rules['location_mode']=='Supplied candidates' and 'candidates' not in paths:raise ValueError('Supplied candidates mode requires a candidate source.')
            cfg={'operation':'plan','paths':paths,'rules':rules,'mappings':maps,'output_dir':str(base), 'layer_provenance':provenance}
            config_path=job/'job.json';config_path.write_text(json.dumps(cfg,indent=2),encoding='utf-8');self.job=config_path
            dialog.close();dialog=None;self.launch(['--job',str(config_path)]);self.status.setText('Planning in an isolated Python process...')
        except Exception as exc:
            if dialog is not None:dialog.close()
            if self.job and self.job.parent.name.startswith('aaf_qgis_job_'):shutil.rmtree(self.job.parent,ignore_errors=True)
            self.problem(exc)

    def fetch_osm(self):
        if self.busy():return
        try:
            self.executable();canvas=self.iface.mapCanvas();tr=QgsCoordinateTransform(canvas.mapSettings().destinationCrs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance())
            p=tr.transform(canvas.center());base=Path(self.output.text()).expanduser().resolve();job=base/'qgis_jobs'/('fetch_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'));job.mkdir(parents=True)
            target=base/'building_inputs'/('osm_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.geojson')
            cfg={'operation':'fetch_osm','latitude':p.y(),'longitude':p.x(),'radius_km':self.osm_radius.value(),'target':str(target)}
            self.job=job/'job.json';self.job.write_text(json.dumps(cfg),encoding='utf-8');self.launch(['--job',str(self.job)]);self.status.setText('Fetching a bounded OSM building area around the map centre...')
        except Exception as exc:self.problem(exc)

    def read_stdout(self):
        if self.process is None:return
        self.stdout+=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        while '\n' in self.stdout:
            line,self.stdout=self.stdout.split('\n',1)
            if not line.strip():continue
            try:self.message(json.loads(line))
            except json.JSONDecodeError:self.log.appendPlainText(line)
            except Exception as exc:self.error_seen=True;self.status.setText('Output handling failed: '+str(exc));self.log.appendPlainText(traceback.format_exc())

    def read_stderr(self):
        if self.process is not None:
            text=bytes(self.process.readAllStandardError()).decode('utf-8',errors='replace')
            if text.strip():self.log.appendPlainText(text)

    def message(self,data):
        event=data.get('event')
        if event=='log':self.log.appendPlainText(data['message']);self.status.setText(data['message'])
        elif event=='error':self.error_seen=True;self.log.appendPlainText(data.get('traceback',data['message']));self.status.setText(data['message'])
        elif event=='dependencies':self.log.appendPlainText(json.dumps(data,indent=2));self.status.setText('Planning dependencies are available.')
        elif event=='result':
            self.result=data;self.load_result(data);s=data['summary'];self.result_label.setText(f"{s['proposed_sites']} proposed sites; {s['assigned_grids']:,} grids with assigned demand. Mode: {s['mode']}.\n"+'\n'.join(s['notes']));self.status.setText('Complete. Results added to QGIS.')
        elif event=='fetch_result':
            self.sources['buildings']['path'].setText(data['path']);self.sources['buildings']['mode'].setCurrentText('File');self.status.setText(f"Loaded {data['count']} mapped OSM footprints. Rerun planning to apply building rules.")
            layer=QgsVectorLayer(data['path'],'OSM building input','ogr')
            if layer.isValid():QgsProject.instance().addMapLayer(layer)

    def process_error(self,error):
        self.error_seen=True;self.status.setText('External Python could not start or crashed. Check its path and Results/log.');self.log.appendPlainText(str(error));self.set_busy(False)
    def finished(self,code,status):
        self.read_stdout();self.read_stderr();self.set_busy(False)
        if code and not self.error_seen:self.status.setText('Planning stopped. See Results/log.');self.log.appendPlainText('Worker exit code: '+str(code))
        self.persist()
        if self.job and self.job.parent.name.startswith('aaf_qgis_job_'):shutil.rmtree(self.job.parent,ignore_errors=True)
        completed=self.process;self.process=None
        if completed is not None:completed.deleteLater()

    def cancel(self):
        if not self.busy():return
        if self.job:self.job.with_name('cancel').write_text('cancel',encoding='utf-8')
        self.status.setText('Cancellation requested. No incomplete result will be loaded.');process=self.process
        QTimer.singleShot(15000,lambda:process.kill() if self.process is process and self.busy() else None)

    def load_result(self,data):
        project=QgsProject.instance();group=project.layerTreeRoot().insertGroup(0,'New Site Planning '+Path(data['folder']).name)
        site=None
        order=['sites','no_build_polygons','buildings','airtel','competitors','grids','demand_points']
        for key in order:
            path=data.get('layers',{}).get(key)
            if not path:continue
            layer=QgsVectorLayer(path,{'sites':'Proposed sites','grids':'Grid assessment','airtel':'Airtel active / pipeline / legacy','competitors':'Competitor inventory','buildings':'Mapped building footprints','demand_points':'Demand representation','no_build_polygons':'No-build polygon constraints'}[key],'ogr')
            if not layer.isValid():self.log.appendPlainText('Could not load output: '+path);continue
            if layer.featureCount()==0:self.log.appendPlainText('Empty output layer: '+key);continue
            self.style(layer,key);project.addMapLayer(layer,False);node=group.addLayer(layer)
            if key in ('competitors','demand_points'):node.setItemVisibilityChecked(False)
            if key=='sites':site=layer
        if site is not None and site.featureCount()>0:
            self.iface.setActiveLayer(site);extent=QgsCoordinateTransform(site.crs(),self.iface.mapCanvas().mapSettings().destinationCrs(),project).transformBoundingBox(site.extent());extent.scale(1.15);self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()

    def style(self,layer,key):
        polygon=QgsWkbTypes.geometryType(layer.wkbType())==QgsWkbTypes.PolygonGeometry
        if key=='grids':
            categories=[]
            for value,color in [('Red','#d92f45'),('Amber','#e6a21c'),('Green','#279b67'),('Grey','#959ca5')]:
                symbol=QgsFillSymbol.createSimple({'color':color,'outline_color':'#88939d','outline_width':'.1'}) if polygon else QgsMarkerSymbol.createSimple({'name':'circle','color':color,'size':'1.3','outline_style':'no'})
                categories.append(QgsRendererCategory(value,symbol,value))
            layer.setRenderer(QgsCategorizedSymbolRenderer('category',categories));layer.setOpacity(.45)
        elif key=='airtel':
            categories=[]
            for value,color in [('Active 4G/5G','#155fac'),('Reserved pipeline','#e97f22'),('Legacy/off-air','#888888')]:
                categories.append(QgsRendererCategory(value,QgsMarkerSymbol.createSimple({'name':'triangle','color':color,'size':'2.3'}),value))
            layer.setRenderer(QgsCategorizedSymbolRenderer('planning_role',categories))
        elif key=='no_build_polygons':layer.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'#e86264','outline_color':'#a32429','outline_width':'.5'}));layer.setOpacity(.35)
        elif key=='buildings':layer.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'#ba9870','outline_color':'#674c35','outline_width':'.15'}));layer.setOpacity(.6)
        else:
            color='#7a2ab7' if key=='sites' else '#555555' if key=='competitors' else '#158b9c'
            symbol=QgsMarkerSymbol.createSimple({'name':'diamond' if key=='sites' else 'circle','color':color,'size':'4' if key=='sites' else '1.6'})
            layer.renderer().setSymbol(symbol)
            if key=='sites':
                settings=QgsPalLayerSettings();settings.fieldName='candidate_id';fmt=QgsTextFormat();fmt.setSize(8);fmt.setColor(QColor('#401557'));settings.setFormat(fmt)
                layer.setLabeling(QgsVectorLayerSimpleLabeling(settings));layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def add_osm(self):
        layer=QgsRasterLayer('type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmin=0&zmax=19','OpenStreetMap © contributors','wms')
        if not layer.isValid():self.problem('Could not create the OSM XYZ layer. Check network/provider availability.');return
        project=QgsProject.instance();project.addMapLayer(layer,False);project.layerTreeRoot().addLayer(layer)
        self.iface.messageBar().pushInfo('OpenStreetMap','© OpenStreetMap contributors — interactive tiles; no bulk download/prefetch.')
    def open_folder(self):
        if self.result:QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['folder']))
    def open_web(self):
        if not self.result:self.problem('Run planning first.');return
        try:
            if self.map_process:self.map_process.kill();self.map_process.waitForFinished(2000)
            self.map_process=QProcess(self);env=QProcessEnvironment.systemEnvironment()
            for key in ['PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','PROJ_LIB','PROJ_DATA','GDAL_DATA']:env.remove(key)
            self.map_process.setProcessEnvironment(env);self.map_process.start(self.executable(),[str(ROOT/'core'/'review_project.py'),'--folder',self.result['folder']])
        except Exception as exc:self.problem(str(exc))
    def open_earth(self):
        if self.result:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result['folder'])/'planning_google_earth.kmz')))
    def open_help(self):QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT/'README.html')))
    def shutdown(self):
        self.persist()
        if self.busy():self.process.kill();self.process.waitForFinished(2000)
        if self.server:self.server.close();self.server=None
        if self.map_process:self.map_process.kill();self.map_process.waitForFinished(2000);self.map_process=None
