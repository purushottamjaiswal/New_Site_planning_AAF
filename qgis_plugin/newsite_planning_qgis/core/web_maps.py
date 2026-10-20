"""Official-provider web views plus portable Google Earth KML/KMZ review layers."""
from pathlib import Path
import json,math,zipfile,xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from functools import partial
import threading
from map_tools import embed_tools


def feature_points(frame,kind):
    result=[]
    for row in json.loads(frame.to_json(orient='records')):
        result.append({'type':'Feature','geometry':{'type':'Point','coordinates':[row['lon'],row['lat']]},
          'properties':{'layer':kind,**row}})
    return {'type':'FeatureCollection','features':result}


def map_data(plan):
    buildings=getattr(plan,'buildings_context',None)
    context=getattr(plan,'polygon_context',None)
    grid_features=None;no_build=[]
    if context is not None:
        from shapely.geometry import mapping
        rows={r['id']:r for r in json.loads(plan.grids.to_json(orient='records'))}
        grid_features=[{'type':'Feature','geometry':mapping(g),'properties':{'layer':'grids',**rows[k]}} for k,g in context.grids.items() if k in rows]
        no_build=[{'type':'Feature','geometry':mapping(g),'properties':{'layer':'no_build','id':i+1}} for i,g in enumerate(context.exclusions)]
    if not grid_features:
        from terrain_masks import nominal_grid_polygon
        from shapely.geometry import mapping
        records=json.loads(plan.grids.to_json(orient='records'))
        grid_features=[{'type':'Feature','geometry':mapping(nominal_grid_polygon(row['lat'],row['lon'],1000 if plan.rules.grid_size=='1km' else 3000)),'properties':{'layer':'grids',**row,'geometry_basis':'Reconstructed nominal cell from source centroid and grid size'}} for row in records]
    data={'summary':plan.summary,'rules':vars(plan.rules),'layers':{
      'sites':feature_points(plan.sites,'sites'),
      'airtel':feature_points(plan.airtel,'airtel'),
      'competitors':feature_points(plan.competitors,'competitors'),
      'grids':{'type':'FeatureCollection','features':grid_features} if grid_features else feature_points(plan.grids[['id','lat','lon','category','town','assigned_candidate']],'grids'),
      'no_build':{'type':'FeatureCollection','features':no_build},
      'buildings':{'type':'FeatureCollection','features':buildings.features[:5000] if buildings else []}},
      'building_metadata':buildings.metadata if buildings else {},'building_total':len(buildings.features) if buildings else 0}
    return data


def write_web_map(plan,path):
    data=map_data(plan)
    encoded=json.dumps(data,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    Path(path).write_text(embed_tools(TEMPLATE).replace('__DATA__',encoded),encoding='utf-8')


def write_earth(plan,path):
    NS='http://www.opengis.net/kml/2.2';ET.register_namespace('',NS)
    root=ET.Element('{'+NS+'}kml');doc=ET.SubElement(root,'Document');ET.SubElement(doc,'name').text='New site planning — engineering review'
    ET.SubElement(doc,'description').text='Proposed/active/planned sites and open footprint context. No survey, roof strength, imagery analysis or RF coverage certification is implied.'
    def folder(name):
        f=ET.SubElement(doc,'Folder');ET.SubElement(f,'name').text=name;return f
    for frame,title in [(plan.sites,'Proposed sites'),(plan.airtel,'Airtel active / reserved / legacy'),(plan.competitors,'Historical competitor sites')]:
        f=folder(title)
        for row in json.loads(frame.to_json(orient='records')):
            pm=ET.SubElement(f,'Placemark');ET.SubElement(pm,'name').text=str(row.get('candidate_id') or row.get('id','Site'))
            ET.SubElement(pm,'description').text='\n'.join(f'{k}: {v}' for k,v in row.items() if v is not None)
            point=ET.SubElement(pm,'Point');ET.SubElement(point,'altitudeMode').text='clampToGround';ET.SubElement(point,'coordinates').text=f"{row['lon']},{row['lat']},0"
    buildings=getattr(plan,'buildings_context',None)
    if buildings:
        f=folder('Open building footprints — preview (maximum 5,000; full GeoJSON export supplied)')
        for feat in buildings.features[:5000]:
            pm=ET.SubElement(f,'Placemark');p=feat['properties'];ET.SubElement(pm,'name').text=str(p.get('building_id','Building'))
            ET.SubElement(pm,'description').text='\n'.join(f'{k}: {v}' for k,v in p.items())
            geom=feat['geometry'];polys=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates'];parent=pm
            if len(polys)>1:parent=ET.SubElement(pm,'MultiGeometry')
            for coords in polys:
                poly=ET.SubElement(parent,'Polygon');ET.SubElement(poly,'altitudeMode').text='clampToGround'
                for i,ring in enumerate(coords):
                    boundary=ET.SubElement(poly,'outerBoundaryIs' if i==0 else 'innerBoundaryIs');linear=ET.SubElement(boundary,'LinearRing')
                    ET.SubElement(linear,'coordinates').text=' '.join(f'{c[0]},{c[1]},0' for c in ring)
    path=Path(path);ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    with zipfile.ZipFile(path.with_suffix('.kmz'),'w',zipfile.ZIP_DEFLATED) as z:z.write(path,'doc.kml')


class LocalMapServer:
    def __init__(self,folder):
        folder=Path(folder).resolve();index=None;planning_db=None
        try:
            settings=json.loads((folder/'run_settings.json').read_text())
            planning_db=settings.get('result_database') or settings.get('temporary_database') or settings.get('summary',{}).get('database_path')
            if planning_db and not Path(planning_db).is_absolute():planning_db=str(folder/planning_db)
            if planning_db and not Path(planning_db).is_file():planning_db=None
            candidate=settings.get('source_layers_and_crs',{}).get('buildings',{}).get('index_path')
            if candidate and not Path(candidate).is_absolute():candidate=str(folder/candidate)
            if candidate and Path(candidate).is_file():
                from building_index import DiskBuildingContext
                index=DiskBuildingContext(candidate)
        except (ValueError,OSError):pass
        review_lock=threading.Lock()
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                from urllib.parse import urlparse
                from contextlib import closing
                if urlparse(self.path).path not in ('/api/review/preview','/api/review/save'):self.send_error(404);return
                if self.headers.get('Origin') and self.headers['Origin']!='http://'+self.headers.get('Host',''):self.send_error(403);return
                try:
                    if not planning_db:raise ValueError('Re-export with persistent result project enabled.')
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=16384 or self.headers.get_content_type()!='application/json':raise ValueError('Expected a small JSON review request.')
                    from project_review import ProjectSession,evaluate,save_review
                    request=json.loads(self.rfile.read(length))
                    with review_lock:
                        with closing(ProjectSession(planning_db)) as project:report=(save_review if self.path.endswith('/save') else evaluate)(project,request)
                    payload=json.dumps(report,allow_nan=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
                except (ValueError,KeyError,TypeError,OSError) as exc:
                    payload=json.dumps({'error':str(exc)}).encode();self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                except Exception as exc:self.send_error(503,'Project review is unavailable; re-export with the updated application.')

            def do_GET(self):
                from urllib.parse import urlparse,parse_qs
                request=urlparse(self.path)
                if request.path=='/api/alternatives':
                    try:
                        import sqlite3
                        from contextlib import closing
                        key=parse_qs(request.query).get('site_id',[''])[0]
                        if not planning_db or not key:raise ValueError()
                        with closing(sqlite3.connect(Path(planning_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:rows=db.execute("SELECT payload FROM alternatives WHERE json_extract(payload,'$.selected_candidate')=? LIMIT 3",(key,)).fetchall()
                        features=[]
                        for text, in rows:
                            p=json.loads(text);features.append({'type':'Feature','properties':{**p,'layer':'alternatives'},'geometry':{'type':'Point','coordinates':[p['lon'],p['lat']]}})
                        payload=json.dumps({'type':'FeatureCollection','features':features}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    except Exception:self.send_error(503,'Alternative comparison unavailable.')
                    return
                if request.path=='/api/reviews':
                    try:
                        import sqlite3
                        from contextlib import closing
                        query=parse_qs(request.query);key=query.get('site_id',[''])[0]
                        if not planning_db:raise ValueError()
                        with closing(sqlite3.connect(Path(planning_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                            rows=db.execute('SELECT revision,record FROM reviews '+('WHERE site_id=? ' if key else '')+'ORDER BY revision DESC LIMIT 100',(key,) if key else ()).fetchall()
                        payload=json.dumps([{'revision':revision,**json.loads(record)} for revision,record in rows]).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    except Exception:self.send_error(503,'Persistent project review history unavailable.')
                    return
                if request.path=='/api/search':
                    import sqlite3
                    from contextlib import closing
                    try:
                        if planning_db is None:self.send_error(503,'Temporary planning session unavailable.');return
                        query=parse_qs(request.query);needle=query.get('id',[''])[0].strip();layer=query.get('layer',['all'])[0]
                        if not needle or len(needle)>256 or layer not in ('all','sites','grids','airtel','competitors'):raise ValueError()
                        features=[]
                        with closing(sqlite3.connect(Path(planning_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                            db.execute('PRAGMA cache_size=-4096')
                            for table in (('sites','grids','airtel','competitors') if layer=='all' else (layer,)):
                                rows=db.execute(f'SELECT payload,geometry FROM {table} WHERE gid=? COLLATE NOCASE ORDER BY rid LIMIT ?', (needle,20-len(features))).fetchall()
                                for text,geometry in rows:
                                    properties=json.loads(text);properties['layer']=table
                                    geom=json.loads(geometry) if geometry else {'type':'Point','coordinates':[properties['lon'],properties['lat']]}
                                    features.append({'type':'Feature','geometry':geom,'properties':properties})
                                if len(features)>=20:break
                        payload=json.dumps({'type':'FeatureCollection','features':features,'limit':20}).encode()
                        self.send_response(200);self.send_header('Content-Type','application/geo+json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    except (ValueError,KeyError):self.send_error(400,'Enter an ID up to 256 characters and a valid search layer.')
                    except sqlite3.Error:self.send_error(503,'Temporary planning session unavailable.')
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                if request.path=='/api/planning':
                    try:
                        import sqlite3
                        from contextlib import closing
                        if planning_db is None:self.send_error(503,'Temporary planning database is no longer available. Use full exported files or embedded previews.');return
                        query=parse_qs(request.query);layer=query.get('layer',[''])[0]
                        if layer not in ('sites','grids','airtel','competitors','no_build','land','water'):raise ValueError()
                        bbox=tuple(map(float,query.get('bbox',[''])[0].split(',')))
                        if len(bbox)!=4 or not all(math.isfinite(v) for v in bbox):raise ValueError()
                        w,south,e,n=bbox
                        if not(-180<=w<e<=180 and -90<=south<n<=90):raise ValueError()
                        with closing(sqlite3.connect(Path(planning_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                            db.execute('PRAGMA cache_size=-4096')
                            records=db.execute(f'SELECT t.payload,t.geometry FROM {layer}_rt b JOIN {layer} t ON b.rid=t.rid WHERE b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=? ORDER BY t.rid LIMIT 5001',(w,e,south,n)).fetchall()
                        features=[]
                        for text,geometry in records[:5000]:
                            properties=json.loads(text);properties['layer']=layer
                            geom=json.loads(geometry) if geometry else {'type':'Point','coordinates':[properties['lon'],properties['lat']]}
                            features.append({'type':'Feature','geometry':geom,'properties':properties})
                        payload=json.dumps({'type':'FeatureCollection','features':features,'truncated':len(records)>5000}).encode()
                        self.send_response(200);self.send_header('Content-Type','application/geo+json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    except (ValueError,KeyError):self.send_error(400,'Invalid planning layer/bbox.')
                    except sqlite3.Error:self.send_error(503,'Temporary planning database is no longer available.')
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                if request.path=='/api/buildings':
                    try:
                        if index is None:self.send_error(503,'Building index unavailable; embedded preview remains available.');return
                        bbox=tuple(map(float,parse_qs(request.query).get('bbox',[''])[0].split(',')))
                        if len(bbox)!=4 or not all(math.isfinite(v) for v in bbox):raise ValueError()
                        w,s,e,n=bbox
                        if not(-180<=w<e<=180 and -90<=s<n<=90) or e-w>1 or n-s>1:self.send_error(400,'Zoom in to an area smaller than one degree.');return
                        from itertools import islice
                        features=list(islice(index.iter_features(bbox),5001));truncated=len(features)>5000
                        payload=json.dumps({'type':'FeatureCollection','features':features[:5000],'truncated':truncated,'total_indexed':index.count}).encode()
                        self.send_response(200);self.send_header('Content-Type','application/geo+json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    except (ValueError,KeyError):self.send_error(400,'Invalid building bbox.')
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                try:super().do_GET()
                except (BrokenPipeError,ConnectionResetError):pass
        try:self.server=ThreadingHTTPServer(('127.0.0.1',8765),partial(Handler,directory=str(Path(folder).resolve())))
        except OSError:self.server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(Path(folder).resolve())))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}/planning_openlayers_map.html'
    def close(self):self.server.shutdown();self.server.server_close()


TEMPLATE=r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Site planning | OSM and Google</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>*{box-sizing:border-box}body{margin:0;font:14px system-ui;background:#eef2f7;color:#172740}header{padding:15px 22px;background:#192c45;color:white}h1{margin:0;font-size:22px}main{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - 77px)}aside{padding:15px;background:white;overflow:auto}.map{height:100%;width:100%}#google{display:none}#view{position:relative;min-width:0}label{display:block;margin:10px 0}button,select,input{padding:8px;font:inherit;border:1px solid #b9c7d8;border-radius:5px}button{cursor:pointer;background:#edf2fa}section{border-top:1px solid #e0e6ef;margin-top:15px;padding-top:12px}.small{font-size:12px;line-height:1.5}#status{padding:8px;background:#fff1cc;white-space:pre-wrap}#info{white-space:pre-wrap;overflow-wrap:anywhere}#siteList button{display:block;width:100%;margin-top:5px;text-align:left;font-size:12px}a{color:#1b57a3}#googleKey{width:100%}</style></head>
<body><header><h1>Site planning — buildings and imagery review</h1><div id="subtitle"></div></header><main><aside>
<label>Map provider <select id="provider"><option value="osm">OpenStreetMap</option><option value="google">Google Maps road / satellite / terrain</option></select></label>
<div class="small">Google requires your Maps JavaScript API key and enabled billing. Enter it for this page session; it is not saved.</div><input id="googleKey" type="password" autocomplete="off" placeholder="Google Maps API key"><button id="loadGoogle" style="margin-top:6px">Load Google map</button>
__MAP_TOOLS_HTML__<p id="planningPreview" class="small"></p><section><strong>Planning overlays</strong><label><input id="sites" type="checkbox" checked> Proposed sites</label><label><input id="airtel" type="checkbox" checked> Airtel inventory / reserved sites</label><label><input id="competitors" type="checkbox"> Competitor sites</label><label><input id="grids" type="checkbox"> Grids / KPI categories</label><label><input id="no_build" type="checkbox" checked> No-build polygons</label><label><input id="buildings" type="checkbox" checked> Open building footprints</label></section>
<section><a href="planning_google_earth.kmz" download>Download Google Earth KMZ</a> · <a href="planning_map.html">Offline map</a><p class="small">Import the KMZ into Google Earth Pro or Earth web to inspect sites and building outlines on its imagery/terrain. Google Earth imagery is not downloaded or analysed by this application.</p></section>
<div id="status" class="small">Loading OpenStreetMap viewer…</div><section><strong>Selected location</strong><div id="info" class="small"></div><p><a id="googleLink" target="_blank" rel="noopener">Open in Google Maps</a></p><a href="https://earth.google.com/web/" target="_blank" rel="noopener">Open Google Earth</a></section>
<section class="small">Building outlines are mapped/inferred evidence, not certified roof or land suitability. Rooftop search points require structural, RF and acquisition review. Empty building coverage can reflect incomplete mapping. Existing/planned sites remain visible.</section><div id="sources" class="small"></div><section><strong>Ranked proposed sites</strong><div id="siteList"></div></section></aside><div id="view"><div id="osm" class="map"></div><div id="google" class="map"></div></div></main>
<script>__MAP_TOOLS_JS__
const D=__DATA__;
const $=id=>document.getElementById(id);const keys=['sites','airtel','competitors','grids','buildings','no_build'];let osm=null,gm=null,googleLoaded=false,leafletLayers={},googleLayers={},selected=null;
function color(p){if(p.layer==='no_build')return '#cf2939';if(p.layer==='sites')return '#772bc4';if(p.layer==='airtel')return p.planning_role==='Reserved pipeline'?'#dd6400':'#1465af';if(p.layer==='competitors')return '#505b68';return ({Red:'#dd3446',Amber:'#df9e12',Green:'#269b68',Grey:'#939ca8'})[p.category]||'#455974'}
function selectFeature(f){selected=f;if(typeof tools!=='undefined')tools.select(f);let p=f.properties;const text=Object.entries(p).map(([k,v])=>k+': '+(v===null?'Unknown':v)).join('\n');$('info').textContent=text;let xy=f.geometry.type==='Point'?f.geometry.coordinates:null;if(xy){$('googleLink').href='https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(xy[1]+','+xy[0]);$('googleLink').textContent='Open selected location in Google Maps'}}
function locate(f){tools.choose(f)}
function flyFeature(f){selectFeature(f);const xy=MapTools.center(f);if(!xy)return;let[lon,lat]=xy;if(osm)osm.flyTo([lat,lon],f.properties.layer==='grids'?15:17,{duration:.65});if(gm){gm.panTo({lat,lng:lon});gm.setZoom(f.properties.layer==='grids'?15:17)}}
function osmInit(){if(!window.L){$('status').textContent='Leaflet could not load. Check internet/proxy, use Google Maps or open the offline map.';return}
osm=L.map('osm',{preferCanvas:true}).setView([0,20],4);osm.on('click',e=>tools.add([e.latlng.lng,e.latlng.lat]));
const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(osm);
let failed=false;tiles.on('tileerror',()=>{if(!failed){failed=true;$('status').textContent='Some OSM tiles failed. Check connectivity/provider limits. Planning overlays remain available.'}});
for(const key of keys){leafletLayers[key]=L.geoJSON(D.layers[key],{style:f=>({color:f.properties.layer==='grids'?color(f.properties):f.properties.layer==='no_build'?'#cf2939':'#79543b',weight:1,fillOpacity:.25}),pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:key==='sites'?6:key==='grids'?2:3,color:color(f.properties),weight:1,fillOpacity:.8}),onEachFeature:(f,layer)=>layer.on('click',e=>{if(!tools.state.measuring)selectFeature(f)})});if($(key).checked)leafletLayers[key].addTo(osm)}
let positions=D.layers.sites.features.map(f=>[f.geometry.coordinates[1],f.geometry.coordinates[0]]);if(!positions.length)positions=D.layers.grids.features.filter(f=>f.geometry.type==='Point').map(f=>[f.geometry.coordinates[1],f.geometry.coordinates[0]]);if(positions.length)osm.fitBounds(positions,{padding:[35,35],maxZoom:16});
tools.refresh();$('status').textContent='OpenStreetMap online tiles. Footprints used: '+D.layers.buildings.features.length+'. No imagery extraction or terrain simulation.'}
function syncLayers(){for(const key of keys){if(osm&&leafletLayers[key]){if($(key).checked)leafletLayers[key].addTo(osm);else osm.removeLayer(leafletLayers[key])}if(googleLayers[key])googleLayers[key].setMap($(key).checked?gm:null)}if(typeof tools!=='undefined')tools.refresh()}
function switchMap(value){$('osm').style.display=value==='osm'?'block':'none';$('google').style.display=value==='google'?'block':'none';if(value==='osm'&&osm)setTimeout(()=>osm.invalidateSize(),20);if(value==='google'&&!googleLoaded)$('status').textContent='Enter a Google Maps JavaScript API key and click Load Google map.'}
window.gm_authFailure=()=>{$('status').textContent='Google API authorization failed. Check API enablement, billing and HTTP referrer restrictions for http://127.0.0.1:8765/*.'};
window.initPlanningGoogle=()=>{try{let c=osm?osm.getCenter():{lat:0,lng:20};gm=new google.maps.Map($('google'),{center:{lat:c.lat,lng:c.lng},zoom:osm?osm.getZoom():5,mapTypeId:'hybrid',mapTypeControl:true});for(const key of keys){let layer=new google.maps.Data();layer.addGeoJson(D.layers[key]);layer.setStyle(feature=>({fillColor:key==='buildings'?'#c6a374':color({layer:key,category:feature.getProperty('category'),planning_role:feature.getProperty('planning_role')}),fillOpacity:key==='buildings'?.3:.75,strokeColor:key==='buildings'?'#67452b':'#4a4d63',strokeWeight:1,icon:['buildings','no_build'].includes(key)?null:{path:google.maps.SymbolPath.CIRCLE,scale:key==='sites'?6:key==='grids'?2:3,fillOpacity:.9,fillColor:color({layer:key,category:feature.getProperty('category'),planning_role:feature.getProperty('planning_role')}),strokeWeight:1}}));layer.addListener('click',e=>{if(!tools.add([e.latLng.lng(),e.latLng.lat()]))e.feature.toGeoJson(selectFeature)});googleLayers[key]=layer}gm.addListener('click',e=>tools.add([e.latLng.lng(),e.latLng.lat()]));googleLoaded=true;syncLayers();$('status').textContent='Google Maps official API. Use its map-type control for road, satellite, hybrid or terrain. Imagery is visual context only.';if(selected)locate(selected)}catch(e){$('status').textContent='Google map initialization failed: '+e.message}};
$('loadGoogle').onclick=()=>{if(googleLoaded){$('provider').value='google';switchMap('google');return}let key=$('googleKey').value.trim();if(!key){$('status').textContent='Provide your Google Maps API key. OSM and Google Earth export do not need it.';return}if(document.getElementById('googleApiScript')){$('status').textContent='Google API is already loading. If it failed, reload this page to retry.';return}$('provider').value='google';switchMap('google');let script=document.createElement('script');script.id='googleApiScript';script.async=true;script.src='https://maps.googleapis.com/maps/api/js?key='+encodeURIComponent(key)+'&loading=async&callback=initPlanningGoogle&v=weekly';script.onerror=()=>{$('status').textContent='Google Maps script failed to load. Check connectivity and API setup.'};document.head.appendChild(script);$('googleKey').value=''};
$('provider').onchange=e=>switchMap(e.target.value);for(const key of keys)$(key).onchange=syncLayers;
$('subtitle').textContent=D.summary.country+' · '+D.summary.grid_period+' · '+D.summary.mode+' · '+D.summary.location_mode;
$('planningPreview').textContent=D.summary.map_preview_limits?'Static preview is capped at 500 planned sites and 5,000 grids/inventory records. Full CSV/GeoJSON exports contain every result. Use the OpenLayers viewer for live viewport queries.':'';
$('sources').textContent='Building source: '+(D.building_metadata.source||'None loaded')+'; license: '+(D.building_metadata.license||'not supplied')+'. Showing '+D.layers.buildings.features.length+' of '+D.building_total+' input footprints (viewer cap 5,000; full footprints exported). '+(D.building_metadata.retrieved_utc?'Retrieved '+D.building_metadata.retrieved_utc:'');
for(const f of D.layers.sites.features){const b=document.createElement('button');b.textContent=f.properties.rank+'. '+f.properties.candidate_id;b.onclick=()=>locate(f);$('siteList').appendChild(b)}

let leafletTools=null,googleTools=[];
function redrawTools(state){if(osm){if(leafletTools)osm.removeLayer(leafletTools);leafletTools=L.layerGroup().addTo(osm)}for(const item of googleTools)item.setMap(null);googleTools=[];
 const points=state.points.map(p=>[p[1],p[0]]);if(osm&&points.length>1)L.polyline(points,{color:'#e44817',weight:3,interactive:false}).addTo(leafletTools);if(gm&&points.length>1)googleTools.push(new google.maps.Polyline({map:gm,path:state.points.map(p=>({lat:p[1],lng:p[0]})),geodesic:true,strokeColor:'#e44817',strokeWeight:3,clickable:false}));
 for(const p of state.points){if(osm)L.circleMarker([p[1],p[0]],{radius:4,color:'#e44817',interactive:false}).addTo(leafletTools)}
 for(const f of state.alternatives||[]){const p=MapTools.center(f);if(osm)L.circleMarker([p[1],p[0]],{radius:7,color:'#d59b00',weight:2,interactive:false}).addTo(leafletTools);if(gm)googleTools.push(new google.maps.Circle({map:gm,center:{lat:p[1],lng:p[0]},radius:20,strokeColor:'#d59b00',fillOpacity:.4,clickable:false}))}
 if(state.draft){const p=state.draft;if(osm)L.circleMarker([p[1],p[0]],{radius:9,color:'#e44817',weight:3,interactive:false}).addTo(leafletTools);if(gm)googleTools.push(new google.maps.Circle({map:gm,center:{lat:p[1],lng:p[0]},radius:30,strokeColor:'#e44817',fillOpacity:.3,clickable:false}))}
 const selected=state.selected,xy=selected&&MapTools.center(selected);if(xy){if(osm)L.circleMarker([xy[1],xy[0]],{radius:12,color:'#e44817',weight:3,fill:false,interactive:false}).addTo(leafletTools);if(gm)googleTools.push(new google.maps.Circle({map:gm,center:{lat:xy[1],lng:xy[0]},radius:30,strokeColor:'#e44817',fillOpacity:0,clickable:false}))}
 if(state.radius&&$('sites').checked){const sites=[...D.layers.sites.features];if(selected&&selected.properties.layer==='sites'&&!sites.some(f=>f.properties.candidate_id===selected.properties.candidate_id))sites.push(selected);for(const f of sites){const xy=MapTools.center(f);if(!xy)continue;const ring=MapTools.ring(xy,D.rules.service_radius_km*1000);if(osm)L.polygon(ring.map(p=>[p[1],p[0]]),{color:'#6551b8',weight:1,dashArray:'6 4',fillOpacity:.06,interactive:false}).addTo(leafletTools);if(gm)googleTools.push(new google.maps.Polygon({map:gm,paths:ring.map(p=>({lat:p[1],lng:p[0]})),geodesic:true,strokeColor:'#6551b8',strokeOpacity:.6,strokeWeight:1,fillColor:'#6551b8',fillOpacity:.06,clickable:false}))}}
}
const tools=MapTools.install({radiusKm:D.rules.service_radius_km,live:!!D.summary.database_path,features:()=>['sites','grids','airtel','competitors'].flatMap(key=>D.layers[key].features),fly:f=>{const key=f.properties.layer;if($(key))$(key).checked=true;syncLayers();flyFeature(f)},redraw:redrawTools});tools.refresh();

let leaflet=document.createElement('script');leaflet.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';leaflet.onload=osmInit;leaflet.onerror=()=>osmInit();document.head.appendChild(leaflet);
</script></body></html>'''
