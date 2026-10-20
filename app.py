"""Tkinter desktop UI. All worker communication goes through a queue."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import fields
from planner import ALIASES, REQUIRED, Rules, read_csv, canonical, run_paths, save_plan
from gis_io import FILE_TYPES, list_layers, read_source, match_mapping, normalize_role, driver_report

from ui_layout import ScrollablePage,initial_window_size

ROOT=Path(__file__).resolve().parent

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('Airtel Africa | New Site Planning v7.0')
        width,height=initial_window_size(self.winfo_screenwidth(),self.winfo_screenheight())
        self.geometry(f'{width}x{height}+20+20');self.minsize(min(680,width),min(420,height));self.resizable(True,True)
        self.columnconfigure(0,weight=1);self.rowconfigure(1,weight=1);self.page_hosts={}
        self.messages=queue.Queue();self.cancelled=threading.Event();self.busy=False;self.last_folder=None;self.web_server=None
        self.current_result=None;self.page_offset=0;self.closing=False;self.source_options={};self.profile_active=None;self.paths={};self.maps={};self.mapboxes={};self.values={};self.summary=tk.StringVar(value='Load sources including required land/water polygons, select scope, then run planning.')
        style=ttk.Style(self);style.theme_use('clam');style.configure('TButton',padding=7);style.configure('Title.TLabel',font=('Segoe UI',19,'bold'));style.configure('TLabelframe.Label',font=('Segoe UI',10,'bold'))
        top=ttk.Frame(self,padding=(16,8));top.grid(row=0,column=0,sticky='ew');top.columnconfigure(0,weight=1)
        ttk.Label(top,text='New Site Planning',style='Title.TLabel').grid(row=0,column=0,sticky='w')
        self.section_var=tk.StringVar();self.section_picker=ttk.Combobox(top,textvariable=self.section_var,state='readonly',width=25)
        self.section_picker.grid(row=0,column=1,sticky='e',padx=(10,0));self.section_picker.bind('<<ComboboxSelected>>',self.choose_section)
        self.tabs=ttk.Notebook(self);self.tabs.grid(row=1,column=0,sticky='nsew',padx=10)
        self.inputs=self.make_page('Data sources');self.rules_tab=self.make_page('Planning rules')
        self.results=self.make_page('Results',scroll=False);self.logs_tab=self.make_page('Run log',scroll=False)
        self.location_tab=self.make_page('Location search',index=2)
        self.mapping_page=self.make_page('Field mappings')
        self.building_tab=self.make_page('Buildings / maps',index=3)
        self.build_inputs();self.build_rules();self.values['mode'].set('Population' if self.paths['population'].get() else 'MR preview');self.build_location_rules();self.build_building_ui();self.build_source_profiles();self.build_results()
        self.logbox=tk.Text(self.logs_tab,wrap='word',font=('Consolas',10));self.logbox.pack(fill='both',expand=True)
        # A separate grid row reserves the footer before the notebook gets spare space.
        bottom=ttk.Frame(self,padding=(12,8));bottom.grid(row=2,column=0,sticky='ew');bottom.columnconfigure(0,weight=1)
        actions=ttk.Frame(bottom);actions.grid(row=0,column=0,sticky='w')
        self.run_button=ttk.Button(actions,text='Run planning',command=self.start);self.run_button.pack(side='left')
        ttk.Button(actions,text='Cancel',command=self.cancelled.set).pack(side='left',padx=5)
        ttk.Button(actions,text='Save scenario',command=self.save_scenario).pack(side='left',padx=5)
        ttk.Button(actions,text='Load scenario',command=self.load_scenario).pack(side='left',padx=5)
        ttk.Label(actions,text='Ctrl+Enter to run').pack(side='left',padx=10)
        self.progress=ttk.Progressbar(bottom,mode='indeterminate');self.progress.grid(row=1,column=0,sticky='ew',pady=(6,4))
        self.status_label=ttk.Label(bottom,textvariable=self.summary,wraplength=max(300,width-32));self.status_label.grid(row=2,column=0,sticky='w')
        bottom.bind('<Configure>',lambda e:self.status_label.configure(wraplength=max(260,e.width-32)))
        self.bind('<Control-Return>',lambda e:self.start() if not self.busy else None)
        self.bind('<MouseWheel>',self.scroll_page);self.bind('<Shift-MouseWheel>',lambda e:self.scroll_page(e,True))
        self.bind('<Button-4>',self.scroll_page);self.bind('<Button-5>',self.scroll_page)
        self.tabs.bind('<<NotebookTabChanged>>',self.sync_section)
        self.sync_section()
        self.protocol('WM_DELETE_WINDOW',self.on_close)
        self.after(100,self.poll)

    def on_close(self):
        self.closing=True
        if self.busy:
            self.cancelled.set();self.summary.set('Closing: cancelling the current task and cleaning temporary data...');self.after(100,self.on_close);return
        if self.web_server:self.web_server.close();self.web_server=None
        if self.current_result and hasattr(self.current_result,'close'):self.current_result.close()
        self.destroy()

    def make_page(self,title,index='end',scroll=True):
        host=ScrollablePage(self.tabs) if scroll else ttk.Frame(self.tabs,padding=12)
        content=host.content if scroll else host
        self.page_hosts[str(content)]=host;self.tabs.insert(index,host,text=title)
        if hasattr(self,'section_picker'):self.section_picker.configure(values=[self.tabs.tab(tab,'text') for tab in self.tabs.tabs()])
        return content

    def select_page(self,content):
        self.tabs.select(self.page_hosts[str(content)])

    def sync_section(self,event=None):
        selected=self.tabs.select()
        if selected:self.section_var.set(self.tabs.tab(selected,'text'))

    def choose_section(self,event=None):
        for tab in self.tabs.tabs():
            if self.tabs.tab(tab,'text')==self.section_var.get():self.tabs.select(tab);break

    def scroll_page(self,event,horizontal=False):
        widget=event.widget
        if isinstance(widget,(tk.Text,ttk.Treeview,ttk.Combobox,tk.Listbox)):return
        while widget is not None:
            if isinstance(widget,ScrollablePage):return widget.scroll(event,horizontal)
            widget=getattr(widget,'master',None)

    def build_inputs(self):
        ttk.Label(self.inputs,text='Select grid, Airtel site master and competitor inputs. Confirm their snapshots before planning.').pack(anchor='w')
        ttk.Label(self.inputs,text='Load real population by grid for Population mode; MR preview uses a proxy. Inputs are staged in temporary databases when planning starts.').pack(anchor='w',pady=(4,10))
        files={'grid':'Postgres_Sep_2026(1).csv','airtel':'Sep_26_SM.csv','competitor':'Comp_data.csv','population':('Population_for_Site_Planner.csv' if (ROOT/'data/Population_for_Site_Planner.csv').exists() else ''),'candidates':'','demand_points':'','exclusions':'','infrastructure':'','no_build_polygons':'','land_polygons':'','water_polygons':'','road_lines':'','backhaul_lines':'','power_lines':'','terrain':'','rf_coverage':''}
        area=ttk.Frame(self.inputs);area.pack(fill='x')
        for i,(kind,filename) in enumerate(files.items()):
            v=tk.StringVar(value=str(ROOT/'data'/filename) if filename else '');self.paths[kind]=v
            ttk.Label(area,text=kind.replace('_',' ').title(),width=15,wraplength=120).grid(row=i,column=0,sticky='w',pady=5)
            ttk.Entry(area,textvariable=v).grid(row=i,column=1,sticky='ew',padx=6)
            ttk.Button(area,text='Browse file',command=lambda k=kind:self.browse(k)).grid(row=i,column=2)
            ttk.Button(area,text='Inspect / map',command=lambda k=kind:self.inspect(k)).grid(row=i,column=3,padx=4)
        area.columnconfigure(1,weight=1)
        box=ttk.LabelFrame(self.mapping_page,text='Column mappings — inspect a source to load its available columns',padding=10);box.pack(fill='both',expand=True,pady=12)
        self.mapping_tabs=ttk.Notebook(box);self.mapping_tabs.pack(fill='both',expand=True)
        for kind,mapping in ALIASES.items():
            panel=ttk.Frame(self.mapping_tabs,padding=8);self.mapping_tabs.add(panel,text=kind.title())
            self.maps[kind]={};self.mapboxes[kind]={}
            for i,(key,col) in enumerate(mapping.items()):
                column=(i//7)*2;row=i%7;v=tk.StringVar(value=col);self.maps[kind][key]=v
                ttk.Label(panel,text=key.replace('_',' ').title()).grid(row=row,column=column,sticky='w',padx=(0,8),pady=4)
                cb=ttk.Combobox(panel,textvariable=v,width=30);cb.grid(row=row,column=column+1,sticky='ew',padx=(0,20),pady=4)
                self.mapboxes[kind][key]=cb
        ttk.Button(self.inputs,text='Create population template for selected country / resolution',command=self.template).pack(anchor='w')
        ttk.Label(self.inputs,text='Required water protection: Land polygons = coastal dry-land boundary; Water polygons = rivers, lakes, lagoons and other water areas. Sources must cover your planning area.\nOptional siting inputs: candidates = feasible tower/rooftop/parcel coordinates; demand_points = finer population/MR points;\nexclusions = circular no-build buffers; infrastructure = Road, Backhaul or Power reference points. Templates are in data/siting_templates.',wraplength=1050).pack(anchor='w',pady=12)
        self.source_info=tk.StringVar(value='Standalone GIS/table reader: CSV, SHP, GPKG, KML/KMZ, GeoJSON/Esri JSON, FileGDB and installed GDAL vector drivers. Use Source layers / CRS to select internal layers.')
        ttk.Label(self.inputs,textvariable=self.source_info,wraplength=1100).pack(anchor='w',pady=8)

    def build_rules(self):
        defaults=Rules()
        left=ttk.LabelFrame(self.rules_tab,text='Scope and demand',padding=14);left.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        right=ttk.LabelFrame(self.rules_tab,text='Candidate selection',padding=14);right.grid(row=0,column=1,sticky='nsew')
        self.rules_tab.columnconfigure(0,weight=1);self.rules_tab.columnconfigure(1,weight=1)
        scope=[('country','Country / OPCO',['NG','CD','CG','GA','KE','MG','MW','NE','RW','TD','TZ','UG','ZM','SC']),
               ('grid_size','Grid resolution',['1km','3km']),('grid_period','Grid snapshot',[]),
               ('airtel_period','Airtel snapshot',[]),('mode','Demand mode',['Population','MR preview']),
               ('population_kind','Population CSV units',['People per grid','People per km2']),
               ('min_density','Minimum people / km²',None),('target_policy','Target demand',['Red + Amber','Red only']),('location_policy','Permitted location grids',['Current behaviour','Target grids only','Red grids only','Any selected grid']),('priority_policy','Selection priority',['Weighted demand','Red demand first'])]
        tuning=[('max_sites','Maximum proposed sites',None),('service_radius_km','Assumed service radius (km)',None),
                ('min_airtel_km','Minimum distance to Airtel (km)',None),('min_new_km','Minimum new-site spacing (km)',None),
                ('competitor_radius_km','Competitor influence radius (km)',None),('competitor_boost','Competitor boost (0.25 = 25%)',None),
                ('red_weight','Red grid weight',None),('amber_weight','Amber grid weight',None),
                ('min_incremental','Minimum incremental people / MR',None),('import_memory_mb','Adaptive import target (MiB)',None),('site_compound_radius_m','Ground compound radius (m)',None),('water_setback_m','Water setback (m)',None),('road_setback_m','Road-line setback (m)',None),('max_slope_deg','Maximum DEM slope (degrees)',None)]
        self.rule_boxes={}
        for panel,items in [(left,scope),(right,tuning)]:
            for i,(key,label,options) in enumerate(items):
                v=tk.StringVar(value=str(getattr(defaults,key)));self.values[key]=v
                ttk.Label(panel,text=label).grid(row=i,column=0,sticky='w',pady=7,padx=(0,8))
                entry=ttk.Combobox(panel,textvariable=v,values=options,width=23) if options is not None else ttk.Entry(panel,textvariable=v,width=25)
                entry.grid(row=i,column=1,sticky='ew',pady=7);self.rule_boxes[key]=entry
        row=len(scope)
        for key,label in [('reserve_planned','Reserve planned and under-deployment Airtel sites'),('respect_site_present','Exclude locations in grids with a site-present flag'),('persist_project','Save a persistent derived-results project'),('export_gpkg','Export GIS results as a GeoPackage'),('adaptive_import','Adapt building batches to the memory target'),('terrain_required','Require DEM terrain screening'),('rf_required','Require validated RF footprints for supplied candidates')]:
            v=tk.BooleanVar(value=getattr(defaults,key));self.values[key]=v
            ttk.Checkbutton(left,text=label,variable=v).grid(row=row,column=0,columnspan=2,sticky='w',pady=8);row+=1
        self.output=tk.StringVar(value=str(ROOT/'outputs'))
        output=ttk.LabelFrame(self.rules_tab,text='Output folder',padding=10);output.grid(row=1,column=0,columnspan=2,sticky='ew',pady=14)
        ttk.Entry(output,textvariable=self.output,width=95).pack(side='left',fill='x',expand=True)
        ttk.Button(output,text='Choose folder',command=self.choose_output).pack(side='left',padx=8)
        info=('Production engine v7.0 is shared with the updated QGIS worker. Older QGIS v1 releases differ and must not be used for equivalent results. New policy choices explicitly control demand targets and placement. '
              'Location search evaluates alternatives or supplied feasible coordinates within target Red/Amber areas. Marginal score = unassigned demand × category weight × competitor factor. '
              'Each detailed demand point is assigned once; coarse demand remains represented at its grid centre. Distances use the Earth sphere. Nearby legacy / off-air sites are shown for review. '
              'Radius is a planning assumption, not predicted coverage. Candidate siting, terrain, capacity, land and backhaul need engineering validation. '
              'Use one resolution per run; overlapping 1 km and 3 km grids must never be added together.')
        ttk.Label(self.rules_tab,text=info,wraplength=1060,justify='left').grid(row=2,column=0,columnspan=2,sticky='w')

    def build_location_rules(self):
        ttk.Label(self.location_tab,text='Choose where the planner may place a site',style='Title.TLabel').pack(anchor='w',pady=8)
        ttk.Label(self.location_tab,text='Supplied candidates gives selection from real input coordinates. Area search creates desktop alternatives and cannot establish land availability or survey accuracy.',wraplength=1080).pack(anchor='w',pady=(0,15))
        self.location_search_enabled=tk.BooleanVar(value=True)
        ttk.Checkbutton(self.location_tab,text='Enable detailed location search (turn off for a quick demo)',variable=self.location_search_enabled,command=self.update_location_search).pack(anchor='w',pady=6)
        self.location_search_status=tk.StringVar()
        ttk.Label(self.location_tab,textvariable=self.location_search_status,wraplength=1070).pack(anchor='w',pady=6)
        self.location_search_widgets={}
        frame=ttk.LabelFrame(self.location_tab,text='Location and feasibility settings',padding=14);frame.pack(fill='x')
        settings=[('location_mode','Location method',['Area search','Settlement search','Supplied candidates','Grid centroid (legacy)']),
          ('search_spacing_m','Area-search spacing (m)',None),('distance_decay','Distance-decay strength (0–10)',None),
          ('max_candidate_points','Maximum location alternatives',None),('import_batch_size','Input batch rows (memory control)',None),('max_road_km','Maximum road reference distance (km)',None),
          ('max_backhaul_km','Maximum backhaul reference distance (km)',None),('max_power_km','Maximum power reference distance (km)',None)]
        defaults=Rules()
        for i,(key,label,options) in enumerate(settings):
            v=tk.StringVar(value=str(getattr(defaults,key)));self.values[key]=v
            ttk.Label(frame,text=label).grid(row=i,column=0,sticky='w',pady=7,padx=(0,20))
            widget=ttk.Combobox(frame,textvariable=v,values=options,width=33,state='readonly') if options else ttk.Entry(frame,textvariable=v,width=35)
            widget.grid(row=i,column=1,sticky='ew',pady=7)
            self.location_search_widgets[key]=widget
        self.update_location_search()
        ttk.Label(self.location_tab,text='Limits of 0 disable the corresponding infrastructure check. An enabled limit requires matching reference data. No-build CSV buffers are always enforced when loaded. Candidate feasibility must explicitly be Yes. Candidate suitability (0–1) adjusts the location score. All distances are recomputed at each alternative coordinate.',wraplength=1070,justify='left').pack(anchor='w',pady=15)
        ttk.Label(self.location_tab,text='For more accurate siting: prepare feasible candidate coordinates from your tower/land/rooftop inventory, exclude water/protected land in GIS, and supply fine demand points. Terrain propagation, antenna design, road/fiber routing and field surveys are not performed by this CSV-based tool.',wraplength=1070,justify='left').pack(anchor='w',pady=8)

    def update_location_search(self):
        enabled=self.location_search_enabled.get()
        for key in ('location_mode','search_spacing_m','max_candidate_points'):
            self.location_search_widgets[key].configure(state=('readonly' if key=='location_mode' else 'normal') if enabled else 'disabled')
        self.location_search_status.set('Detailed planning: evaluate alternative locations using the selected method.' if enabled else 'QUICK DEMO: one approximate centroid per target grid; search limit ignored. Land/water and loaded checks remain active. Rooftop search becomes building context only. Detailed settings are retained when switched back on.')

    def build_building_ui(self):
        ttk.Label(self.building_tab,text='Open building evidence and map review',style='Title.TLabel').pack(anchor='w',pady=8)
        ttk.Label(self.building_tab,text='Load OSM / Google Open Buildings footprints to screen ground-site clearance, assess mapped settlement density, or search building interiors for rooftop review.',wraplength=1080).pack(anchor='w',pady=8)
        self.paths['buildings']=tk.StringVar(value='')
        row=ttk.Frame(self.building_tab);row.pack(fill='x',pady=6)
        ttk.Entry(row,textvariable=self.paths['buildings']).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Load footprint file',command=self.browse_buildings).pack(side='left',padx=5)
        settings=ttk.LabelFrame(self.building_tab,text='Planning evidence rules',padding=12);settings.pack(fill='x',pady=8)
        items=[('building_storage','Building storage',['Disk index']),('building_import_batch_size','Building import batch rows (100–400,000)',None),('building_rule','Building rule',['Ground clearance','Context only','Rooftop search']),
          ('building_clearance_m','Ground-site footprint clearance (m)',None),('building_context_radius_m','Mapped building context radius (m)',None),
          ('building_density_boost','Maximum mapped-density score boost',None),('building_min_confidence','Minimum supplied detection confidence',None),
          ('min_roof_area_m2','Minimum footprint area for roof review (m²)',None),('building_clip_bbox','Optional clip bbox: south,west,north,east',None)]
        defaults=Rules()
        for i,(key,label,options) in enumerate(items):
            self.values[key]=tk.StringVar(value=str(getattr(defaults,key)))
            ttk.Label(settings,text=label).grid(row=i,column=0,sticky='w',padx=(0,15),pady=4)
            entry=ttk.Combobox(settings,textvariable=self.values[key],values=options,state='readonly',width=48) if options else ttk.Entry(settings,textvariable=self.values[key],width=50)
            entry.grid(row=i,column=1,sticky='ew',pady=4)
        fetch=ttk.LabelFrame(self.building_tab,text='Download mapped OSM buildings for one local area',padding=10);fetch.pack(fill='x',pady=8)
        self.fetch_lat=tk.StringVar(value='');self.fetch_lon=tk.StringVar(value='');self.fetch_radius=tk.StringVar(value='1')
        for i,(label,v) in enumerate([('Latitude',self.fetch_lat),('Longitude',self.fetch_lon),('Radius km (0.1–5)',self.fetch_radius)]):
            ttk.Label(fetch,text=label).grid(row=0,column=i*2,padx=4);ttk.Entry(fetch,textvariable=v,width=16).grid(row=0,column=i*2+1,padx=4)
        ttk.Button(fetch,text='Use selected result site',command=self.use_selected_site).grid(row=1,column=0,columnspan=3,sticky='w',pady=6)
        ttk.Button(fetch,text='Fetch OSM footprints',command=self.fetch_buildings).grid(row=1,column=3,columnspan=3,sticky='w',pady=6)
        links=ttk.Frame(self.building_tab);links.pack(fill='x',pady=6)
        ttk.Button(links,text='Google Open Buildings downloads',command=lambda:webbrowser.open('https://sites.research.google/gr/open-buildings/')).pack(side='left')
        ttk.Button(links,text='Open Google Earth',command=lambda:webbrowser.open('https://earth.google.com/web/')).pack(side='left',padx=8)
        ttk.Label(self.building_tab,text='Supported building data: regional GPKG, SHP, complete GDB or readable GDBTABLE, KML/KMZ, GeoJSON/Esri JSON, and WKT CSV/CSV.GZ. Choose the polygon layer in Source layers / CRS. Input rows and spatial indices are staged in temporary databases; screening and exports use bounded batches. Building imports default to 400,000 rows per read batch; reduce this for complex polygons or limited RAM. Other inputs retain their separate batch setting. Temporary data is removed when results are closed. Rooftop search generates unverified footprint interior points when Location method is Area search; supplied candidates retain their original coordinates. OpenLayers provides Google Road/Satellite and OSM backgrounds; the alternate Google API viewer requires its key. Earth review uses exported KML/KMZ. No rooftop strength, imagery extraction or terrain propagation is inferred.',wraplength=1070,justify='left').pack(anchor='w',pady=8)

    def browse_buildings(self):
        if self.busy:return
        self.browse('buildings')

    def use_selected_site(self):
        selection=self.tree.selection()
        if not selection:
            children=self.tree.get_children()
            selection=children[:1]
        if not selection:
            messagebox.showinfo('Area centre','Run planning first and select a result site, or enter a local area centre manually.');return
        values=dict(zip(self.tree['columns'],self.tree.item(selection[0],'values')))
        self.fetch_lat.set(values['lat']);self.fetch_lon.set(values['lon'])

    def fetch_buildings(self):
        if self.busy:return
        try:
            from buildings import area_bbox
            lat=float(self.fetch_lat.get());lon=float(self.fetch_lon.get());radius=float(self.fetch_radius.get());area_bbox(lat,lon,radius)
            target=ROOT/'data'/'buildings'/f'osm_{lat:.5f}_{lon:.5f}_{radius:g}km.geojson'
            self.busy=True;self.run_button.configure(state='disabled');self.progress.start(12)
            self.summary.set('Downloading a bounded OSM building area...')
            def work():
                try:
                    from buildings import fetch_osm
                    count=fetch_osm(lat,lon,radius,target);self.messages.put(('building_done',(target,count)))
                except Exception as exc:self.messages.put(('error',(str(exc),traceback.format_exc())))
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:messagebox.showerror('Building area',str(exc))

    def open_web_map(self):
        if not self.last_folder:messagebox.showinfo('Web map','Run planning first.');return
        try:
            from web_maps import LocalMapServer
            if self.web_server:self.web_server.close()
            self.web_server=LocalMapServer(self.last_folder);webbrowser.open(self.web_server.url)
        except Exception as exc:messagebox.showerror('Web map',str(exc)+'\nPort 8765 must be free. You can also serve the output folder using python -m http.server 8765 --bind 127.0.0.1')

    def build_results(self):
        ttk.Label(self.results,text='Ranked candidate locations — review coordinate basis',style='Title.TLabel').pack(anchor='w')
        buttons=ttk.Frame(self.results);buttons.pack(fill='x',pady=8)
        ttk.Button(buttons,text='Open interactive offline map',command=self.open_map).pack(side='left')
        ttk.Button(buttons,text='OpenLayers / Google maps',command=self.open_web_map).pack(side='left',padx=8)
        ttk.Button(buttons,text='Open output folder',command=self.open_folder).pack(side='left',padx=8)
        columns=('rank','candidate_id','grid_id','town','category','incremental_demand','assigned_grids','nearest_airtel_km','nearest_competitor_km','lat','lon','location_basis','offset_from_grid_centroid_m','location_status','mapped_buildings_within_radius','containing_building_id')
        pages=ttk.Frame(self.results);pages.pack(fill='x',pady=5)
        ttk.Button(pages,text='Previous 200',command=lambda:self.show_result_page(-200)).pack(side='left')
        ttk.Button(pages,text='Next 200',command=lambda:self.show_result_page(200)).pack(side='left',padx=6)
        self.page_info=tk.StringVar(value='Results are paginated.');ttk.Label(pages,textvariable=self.page_info).pack(side='left',padx=8)
        area=ttk.Frame(self.results);area.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(area,columns=columns,show='headings');ys=ttk.Scrollbar(area,orient='vertical',command=self.tree.yview);xs=ttk.Scrollbar(area,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        self.tree.grid(row=0,column=0,sticky='nsew');ys.grid(row=0,column=1,sticky='ns');xs.grid(row=1,column=0,sticky='ew');area.rowconfigure(0,weight=1);area.columnconfigure(0,weight=1)
        for col in columns:self.tree.heading(col,text=col.replace('_',' ').title());self.tree.column(col,width=135)
        self.result_info=tk.StringVar(value='Results include CSVs, site GeoJSON, run settings, data-quality issues and an offline map.')
        ttk.Label(self.results,textvariable=self.result_info,wraplength=1070,justify='left').pack(fill='x',pady=12)

    def show_result_page(self,step=0):
        if self.current_result is None:return
        total=len(self.current_result.sites);self.page_offset=max(0,min(self.page_offset+step,max(0,((total-1)//200)*200)))
        frame=self.current_result.site_page(self.page_offset,200) if hasattr(self.current_result,'site_page') else self.current_result.sites.iloc[self.page_offset:self.page_offset+200]
        self.tree.delete(*self.tree.get_children())
        for row in frame.to_dict('records'):
            values=[]
            for col in self.tree['columns']:
                val=row.get(col,'');values.append(f'{val:.7f}' if col in ('lat','lon') and isinstance(val,float) else f'{val:,.2f}' if isinstance(val,float) else val)
            self.tree.insert('','end',values=values)
        self.page_info.set(f'Showing {self.page_offset+1 if total else 0:,}–{min(total,self.page_offset+200):,} of {total:,}; full results exported.')

    def get_paths(self):return {k:v.get().strip() for k,v in self.paths.items()}
    def get_maps(self):return {k:{f:v.get().strip() for f,v in fields.items()} for k,fields in self.maps.items()}
    def get_rules(self):
        kw={}
        defaults=Rules()
        for field in fields(Rules):
            name=field.name;v=self.values[name].get();default=getattr(defaults,name)
            if not self.location_search_enabled.get() and name in ('location_mode','search_spacing_m','max_candidate_points'):
                v='Quick demo (grid centroid)' if name=='location_mode' else str(default)
            kw[name]=bool(v) if isinstance(default,bool) else int(v) if isinstance(default,int) else float(v) if isinstance(default,float) else str(v).strip()
        if not self.location_search_enabled.get() and kw['building_rule']=='Rooftop search':kw['building_rule']='Context only'
        result=Rules(**kw);result.country=result.country.upper();result.validate();return result

    def build_source_profiles(self):
        self.source_tab=self.make_page('Source layers / CRS',index=1)
        ttk.Label(self.source_tab,text='Choose the layer, source CRS and coordinate handling',style='Title.TLabel').pack(anchor='w',pady=8)
        ttk.Label(self.source_tab,text='A GPKG/GDB/KML can hold multiple layers. Select the layer explicitly. A missing CRS needs its actual EPSG code; do not guess from coordinate ranges.',wraplength=1080).pack(anchor='w',pady=8)
        row=ttk.Frame(self.source_tab);row.pack(fill='x')
        self.profile_kind=tk.StringVar(value='grid');self.profile_combo=ttk.Combobox(row,textvariable=self.profile_kind,values=list(self.paths),state='readonly',width=24);self.profile_combo.pack(side='left')
        self.profile_combo.bind('<<ComboboxSelected>>',lambda e:self.show_source_profile())
        ttk.Button(row,text='Browse file',command=lambda:self.browse(self.profile_kind.get())).pack(side='left',padx=8)
        ttk.Button(row,text='Browse GDB folder',command=self.browse_gdb).pack(side='left',padx=8)
        ttk.Button(row,text='Inspect / list layers',command=lambda:self.inspect(self.profile_kind.get())).pack(side='left',padx=8)
        self.profile_path=tk.StringVar();ttk.Label(self.source_tab,textvariable=self.profile_path,wraplength=1070).pack(anchor='w',pady=10)
        form=ttk.Frame(self.source_tab);form.pack(fill='x')
        self.profile_layer=tk.StringVar();self.profile_crs=tk.StringVar();self.profile_geom=tk.BooleanVar(value=True)
        ttk.Label(form,text='Selected layer').grid(row=0,column=0,sticky='w',pady=8)
        self.profile_layer_box=ttk.Combobox(form,textvariable=self.profile_layer,width=65,state='readonly');self.profile_layer_box.grid(row=0,column=1,sticky='ew',pady=8)
        self.profile_layer_box.bind('<<ComboboxSelected>>',lambda e:self.profile_layer_selected())
        ttk.Label(form,text='Source CRS override (optional)').grid(row=1,column=0,sticky='w',padx=(0,15),pady=8)
        ttk.Entry(form,textvariable=self.profile_crs,width=68).grid(row=1,column=1,sticky='ew',pady=8)
        ttk.Checkbutton(form,text='Derive WGS84 coordinates from source geometry',variable=self.profile_geom).grid(row=2,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Button(form,text='Apply and inspect selected layer',command=lambda:self.inspect(self.profile_kind.get())).grid(row=3,column=0,columnspan=2,sticky='w',pady=8)
        self.layer_info=tk.StringVar(value='No source inspected yet.');ttk.Label(self.source_tab,textvariable=self.layer_info,wraplength=1080).pack(anchor='w',pady=8)
        self.source_preview=tk.Text(self.source_tab,height=13,font=('Consolas',10),wrap='none');self.source_preview.pack(fill='both',expand=True,pady=8)
        ttk.Button(self.source_tab,text='Show installed GIS drivers',command=lambda:self.show_drivers()).pack(anchor='w')
        self.layer_choices={};self.show_source_profile()

    def commit_source_profile(self):
        if self.profile_active:
            self.source_options[self.profile_active]={'layer':self.profile_layer.get().strip() or None,
                'crs_override':self.profile_crs.get().strip() or None,'geometry_coordinates':bool(self.profile_geom.get())}

    def show_source_profile(self):
        self.commit_source_profile();kind=self.profile_kind.get();self.profile_active=kind;opt=self.source_options.get(kind,{})
        self.profile_path.set(self.paths[kind].get());self.profile_layer_box.configure(values=self.layer_choices.get(kind,[]))
        self.profile_layer.set(opt.get('layer') or '');self.profile_crs.set(opt.get('crs_override') or '');self.profile_geom.set(opt.get('geometry_coordinates',True))

    def reset_source_profile(self,kind):
        self.source_options[kind]={};self.layer_choices[kind]=[]
        if self.profile_active==kind:self.profile_active=None
        self.profile_kind.set(kind);self.show_source_profile()

    def get_source_options(self):
        self.commit_source_profile();return json.loads(json.dumps(self.source_options))

    def profile_layer_selected(self):
        self.commit_source_profile()
        if not self.busy:self.inspect(self.profile_kind.get())

    def browse_gdb(self):
        if self.busy:return
        path=filedialog.askdirectory(title='Select the complete .gdb folder')
        if path:
            kind=self.profile_kind.get();self.paths[kind].set(path);self.reset_source_profile(kind);self.inspect(kind)

    def browse(self,kind):
        if self.busy:return
        path=filedialog.askopenfilename(filetypes=FILE_TYPES)
        if path:self.paths[kind].set(path);self.reset_source_profile(kind);self.inspect(kind)

    def inspect(self,kind):
        if self.busy:return
        try:
            opt=self.get_source_options().get(kind,{});path=self.paths[kind].get().strip()
            if not path:raise ValueError('Select a source file or GDB folder first.')
            self.busy=True;self.run_button.configure(state='disabled');self.progress.start(12)
            maps=self.get_maps().get(kind,{})
            def work():
                try:
                    from building_stream import building_layers,building_chunks
                    from stream_io import source_layers,stream_source
                    if kind=='terrain':
                        import rasterio
                        from gis_io import Dataset
                        import pandas as pd
                        with rasterio.open(path) as dem:
                            data=Dataset(pd.DataFrame([{'width':dem.width,'height':dem.height,'crs':str(dem.crs),'nodata':str(dem.nodata)}]),metadata={'driver':dem.driver,'source':path,'source_crs':str(dem.crs)})
                        self.messages.put(('source_layers',(kind,[{'name':'DEM raster'}],'DEM raster',data,{},[])));return
                    layers=building_layers(path) if kind=='buildings' else source_layers(path);names=[v['name'] for v in layers];selected=opt.get('layer')
                    if selected not in names:selected=names[0] if len(names)==1 else None
                    if selected is None:self.messages.put(('source_layers',(kind,layers,None,None,{},[])));return
                    if kind=='buildings' and not str(path).lower().endswith(('.csv','.csv.gz')):
                        data=next(building_chunks(path,layer=selected,crs_override=opt.get('crs_override'),chunk_size=300),None)
                        if data is None:raise ValueError('Selected building layer is empty.')
                    else:data=next(stream_source(path,layer=selected,crs_override=opt.get('crs_override'),batch_size=300,nrows=300),None)
                    if data is None:raise ValueError('Selected source is empty.')
                    mapping,notes=match_mapping(data.frame,kind,maps,ALIASES[kind]) if kind in ALIASES else ({},[])
                    if kind in ALIASES:
                        parent_roles=['id','country','period','size'] if kind=='grid' else []
                        if all(mapping.get(role) in data.frame for role in parent_roles):
                            data.frame,mapping,_=normalize_role(data,kind,mapping,opt)
                    self.messages.put(('source_layers',(kind,layers,selected,data,mapping,notes)))
                except Exception as exc:self.messages.put(('error',(str(exc),traceback.format_exc())))
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:self.busy=False;self.run_button.configure(state='normal');self.progress.stop();messagebox.showerror('Source inspection',str(exc))

    def display_source(self,value):
        kind,layers,selected,data,mapping,notes=value;self.commit_source_profile();self.layer_choices[kind]=[x['name'] for x in layers]
        self.source_options.setdefault(kind,{})['layer']=selected
        self.profile_active=None;self.profile_kind.set(kind);self.show_source_profile();self.select_page(self.source_tab)
        if data is None:
            self.layer_info.set('Multiple layers found. Choose a layer from the dropdown to inspect it.');self.source_preview.delete('1.0','end');self.source_preview.insert('end','\n'.join(str(v) for v in layers));return
        cols=list(data.frame.columns)
        if kind in self.maps:
            for role,box in self.mapboxes[kind].items():
                box.configure(values=['']+cols)
                if role in mapping:self.maps[kind][role].set(mapping[role])
        missing=[role for role in REQUIRED.get(kind,[]) if mapping.get(role) not in cols]
        if kind in ('grid','airtel') and not missing:
            d=canonical(data.frame,kind,mapping)
            period_key='grid_period' if kind=='grid' else 'airtel_period';self.rule_boxes[period_key].configure(values=sorted(d.period.unique()))
        description=f"{kind}: {data.metadata.get('driver')}; layer={selected}; source CRS={data.metadata.get('source_crs') or 'not declared / table'}; preview={len(data.frame)} rows. "
        description+=('Map missing roles: '+', '.join(missing)) if missing else 'Mapped fields available.'
        self.layer_info.set(description);self.source_info.set(description)
        self.source_preview.delete('1.0','end');self.source_preview.insert('end',data.frame.head(8).to_string(index=False)+'\n\n'+'\n'.join(notes))
        self.summary.set('Source inspected. Use Field mappings to adjust columns, then run planning.')

    def show_drivers(self):
        try:
            report=driver_report();self.source_preview.delete('1.0','end');self.source_preview.insert('end',json.dumps(report,indent=2))
        except Exception as exc:messagebox.showerror('GIS drivers',str(exc))

    def choose_output(self):
        value=filedialog.askdirectory()
        if value:self.output.set(value)

    def template(self):
        if self.busy:return
        try:
            rules=self.get_rules();path=self.get_paths()['grid'];options=self.get_source_options();maps=self.get_maps()
            target=filedialog.asksaveasfilename(defaultextension='.csv',initialfile=f'population_{rules.country}_{rules.grid_size}_template.csv',filetypes=[('CSV','*.csv')])
            if not target:return
            self.busy=True;self.cancelled.clear();self.run_button.configure(state='disabled');self.progress.start(12)
            def work():
                from session_db import Session
                import csv
                store=Session(lambda msg:self.messages.put(('log',msg)),self.cancelled.is_set,rules.import_batch_size)
                try:
                    store.ingest({'grid':path},rules,maps,options);count=0
                    with open(target,'w',encoding='utf-8-sig',newline='') as f:
                        writer=csv.writer(f);writer.writerow(['opco','grid_id','latitude','longitude','population'])
                        for _,row,_ in store.input_rows('grid',country=rules.country.upper(),size=rules.grid_size,period=rules.grid_period):writer.writerow([row['country'],row['id'],row['lat'],row['lon'],'']);count+=1
                    if not count:raise ValueError('No grids match the selected country, resolution and snapshot.')
                    self.messages.put(('template_done',(target,count)))
                except Exception as exc:self.messages.put(('error',(str(exc),traceback.format_exc())))
                finally:store.close()
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:messagebox.showerror('Population template',str(exc))

    def save_scenario(self):
        if self.busy:return
        try:
            cfg={'paths':self.get_paths(),'rules':vars(self.get_rules()),'ui_state':{'location_search_enabled':self.location_search_enabled.get(),'detailed_values':{k:self.values[k].get() for k in ('location_mode','search_spacing_m','max_candidate_points','building_rule')}},'mappings':self.get_maps(),'output_dir':self.output.get(),'source_options':self.get_source_options()}
            path=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('Scenario','*.json')])
            if path:Path(path).write_text(json.dumps(cfg,indent=2),encoding='utf-8')
        except Exception as exc:messagebox.showerror('Scenario',str(exc))

    def load_scenario(self):
        if self.busy:return
        path=filedialog.askopenfilename(filetypes=[('Scenario','*.json')])
        if not path:return
        try:
            cfg=json.loads(Path(path).read_text(encoding='utf-8'))
            defaults=Rules()
            for key,value in self.values.items():value.set(getattr(defaults,key))
            for value in self.paths.values():value.set('')
            for k,v in cfg.get('paths',{}).items():
                if k in self.paths:self.paths[k].set(v)
            for k,v in cfg.get('rules',{}).items():
                if k in self.values:self.values[k].set(v)
            ui=cfg.get('ui_state',{});enabled=ui.get('location_search_enabled',cfg.get('rules',{}).get('location_mode','Area search')!='Quick demo (grid centroid)')
            self.location_search_enabled.set(enabled)
            for key,value in ui.get('detailed_values',{}).items():
                if key in self.values:self.values[key].set(value)
            if self.values['location_mode'].get()=='Quick demo (grid centroid)':self.values['location_mode'].set('Area search')
            self.update_location_search()
            for kind,mapping in cfg.get('mappings',{}).items():
                for key,value in mapping.items():
                    if kind in self.maps and key in self.maps[kind]:self.maps[kind][key].set(value)
            self.source_options=cfg.get('source_options',{});self.profile_active=None;self.show_source_profile()
            self.output.set(cfg.get('output_dir',str(ROOT/'outputs')))
        except Exception as exc:messagebox.showerror('Scenario',str(exc))

    def start(self):
        if self.busy:return
        try:
            rules=self.get_rules();paths=self.get_paths();maps=self.get_maps();options=self.get_source_options();output=self.output.get().strip()
            if not output:raise ValueError('Choose an output folder.')
            for key in ('grid','airtel','competitor'):
                if not Path(paths[key]).exists():raise ValueError(f'Select the {key} source file or GDB folder.')
            if rules.mode=='Population' and not paths.get('population'):
                raise ValueError('Load a population CSV for population planning, or select MR preview under Planning rules.')
            if rules.location_mode=='Supplied candidates' and not paths.get('candidates'):
                raise ValueError('Select a candidate CSV for Supplied candidates mode. Templates are in data/siting_templates.')
            self.summary.set('Preflight: '+rules.target_policy+' demand; '+rules.location_policy+' locations; '+rules.priority_policy+'. Working uploads remain temporary; persistent derived results '+('enabled' if rules.persist_project else 'disabled')+'.')
            if rules.mode=='MR preview':
                # Inform, without forcing a repeated confirmation dialog.
                self.summary.set('MR preview selected. Results measure MR records, not people.')
            if self.web_server:self.web_server.close();self.web_server=None
            if self.current_result and hasattr(self.current_result,'close'):self.current_result.close();self.current_result=None
            self.busy=True;self.cancelled.clear();self.run_button.configure(state='disabled');self.progress.start(12)
            self.select_page(self.logs_tab)
            threading.Thread(target=self.worker,args=(paths,rules,maps,output,options),daemon=True).start()
        except Exception as exc:messagebox.showerror('Planning inputs',str(exc))

    def worker(self,paths,rules,maps,output,options):
        result=None
        try:
            result=run_paths(paths,rules,maps,lambda s:self.messages.put(('log',s)),self.cancelled.is_set,source_options=options)
            if self.cancelled.is_set():raise InterruptedError('Planning cancelled. No results exported.')
            folder=save_plan(result,output,paths)
            self.messages.put(('done',(result,folder)))
        except Exception as exc:
            if result is not None and hasattr(result,'close'):result.close()
            self.messages.put(('error',(str(exc),traceback.format_exc())))

    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='log':
                    self.logbox.insert('end',value+'\n');self.logbox.see('end')
                    if int(self.logbox.index('end-1c').split('.')[0])>2000:self.logbox.delete('1.0','501.0')
                else:
                    self.busy=False;self.run_button.configure(state='normal');self.progress.stop()
                    if kind=='error':
                        message,trace=value;self.logbox.insert('end',trace+'\n');self.summary.set(message)
                        if not self.closing:messagebox.showerror('Planning stopped',message)
                    elif kind=='source_layers':self.display_source(value)
                    elif kind=='template_done':self.summary.set(f'Population template saved: {value[1]:,} rows. Fill real population values, then load it as Population.')
                    elif kind=='building_done':
                        target,count=value;self.paths['buildings'].set(str(target));self.reset_source_profile('buildings');self.summary.set(f'{count:,} mapped building footprints loaded. Re-run planning to apply the building rules.');self.select_page(self.building_tab)
                    elif kind=='done':
                        result,folder=value;self.last_folder=folder
                        self.current_result=result;self.page_offset=0;self.show_result_page()
                        self.result_info.set(f'{result.summary["proposed_sites"]} sites; {result.summary["assigned_grids"]:,} assigned grids; '+
                          f'{result.summary["incremental_demand_total"]:,.0f} {result.summary["demand_unit"]}.\n'+
                          f'{result.summary["unassigned_target_grids"]:,} target grids remain partly/fully unassigned. Results: {folder}\n'+
                          '\n'.join(result.summary['notes']))
                        self.summary.set(f'Complete: {len(result.sites)} proposed sites. Open Results to review.');self.select_page(self.results)
        except queue.Empty:pass
        self.after(100,self.poll)

    def open_map(self):
        if self.last_folder:
            try:
                from web_maps import LocalMapServer
                if self.web_server:self.web_server.close()
                self.web_server=LocalMapServer(self.last_folder)
                webbrowser.open(self.web_server.url.replace('planning_openlayers_map.html','planning_map.html'))
            except Exception as exc:messagebox.showerror('Offline map',str(exc))
        else:messagebox.showinfo('Map','Run planning first.')

    def open_folder(self):
        if self.last_folder:
            if os.name=='nt':os.startfile(self.last_folder)
            else:webbrowser.open(self.last_folder.resolve().as_uri())

if __name__=='__main__':App().mainloop()
