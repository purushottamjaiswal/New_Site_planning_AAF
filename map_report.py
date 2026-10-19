"""Self-contained offline geographic overview. No external scripts or tile requests."""
import json
from pathlib import Path

def clean_records(frame, columns):
    cols=[c for c in columns if c in frame]
    return json.loads(frame[cols].to_json(orient='records'))

def write_map(plan, path):
    context=getattr(plan,'buildings_context',None)
    polygons=getattr(plan,'polygon_context',None);actual=[];no_build=[]
    if polygons is not None:
        from shapely.geometry import mapping
        categories=dict(zip(plan.grids.id,plan.grids.category))
        actual=[{'type':'Feature','geometry':mapping(g),'properties':{'id':k,'category':categories.get(k,'Grey')}} for k,g in polygons.grids.items()]
        no_build=[{'type':'Feature','geometry':mapping(g),'properties':{'id':i+1}} for i,g in enumerate(polygons.exclusions)]
    payload={'actual_grid_footprints':actual,'no_build_polygons':no_build,'building_footprints':context.features[:5000] if context else [],'summary':plan.summary,'rules':vars(plan.rules),
      'grids':clean_records(plan.grids,['id','lat','lon','category','demand','density_per_km2',
          'town','status','assigned_candidate','candidate_exclusion','coverage','speed']),
      'sites':clean_records(plan.sites,list(plan.sites.columns)),
      'demand_points':clean_records(plan.demand_points.loc[plan.demand_points.basis=='Supplied detailed demand point'],['point_id','grid_id','lat','lon','demand','assigned_candidate','basis']),
      'airtel':clean_records(plan.airtel,['id','lat','lon','planning_role','technology','status']),
      'competitors':clean_records(plan.competitors,['id','lat','lon','operator','period','technology'])}
    data=json.dumps(payload,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=TEMPLATE.replace('__PAYLOAD__',data)
    Path(path).write_text(html,encoding='utf-8')

TEMPLATE=r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>New Site Planning | Offline map</title><style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif;background:#f3f5f8;color:#142239}
header{padding:20px 26px;background:#182942;color:white;display:flex;justify-content:space-between;align-items:center}
h1{font-size:23px;margin:0 0 5px}h2{font-size:17px}.muted{color:#52647c}header .muted{color:#cad5e4}
main{display:grid;grid-template-columns:310px 1fr;height:calc(100vh - 90px)}aside{padding:18px;background:white;overflow:auto;border-right:1px solid #d5dce5}
label{display:block;margin:9px 0}button,input{font:inherit;padding:8px;border:1px solid #bac8da;border-radius:5px}button{cursor:pointer;background:#eaf0f7}
#mapwrap{position:relative;overflow:hidden;background:#edf1f6}canvas{display:block;touch-action:none}#tip{position:absolute;background:white;border:1px solid #a5b8d2;padding:12px;max-width:350px;pointer-events:none;box-shadow:0 2px 10px #0003;display:none;z-index:4}
.badge{font-size:12px;padding:5px;background:#fff0cc;color:#4d3900;border-radius:4px}.metric{font-size:25px;font-weight:650}section{margin:16px 0;padding-top:8px;border-top:1px solid #e0e5ed}
#siteList button{display:block;text-align:left;margin:5px 0;width:100%;font-size:12px}#detail{white-space:pre-wrap;font-size:12px;line-height:1.6}
#scale{position:absolute;bottom:15px;left:15px;background:white;padding:7px;font-size:12px}.small{font-size:12px;line-height:1.55}.legend span{display:inline-block;width:11px;height:11px;margin-right:5px}
@media(max-width:700px){main{grid-template-columns:230px 1fr}aside{padding:10px}}
</style></head><body><div style="background:#fff1cc;padding:8px;font:12px system-ui">Offline map uses no internet tiles. While the desktop app is open, local viewport queries show grids and sites beyond the initial preview. Full grid polygons: <a href="grid_assessment_spatial.geojson" download>download complete GeoJSON</a>. <span id="viewportStatus"></span></div><header><div><h1>New site planning</h1><div id="subtitle" class="muted"></div></div><div class="badge">Planning candidates • RF validation required</div></header>
<main><aside><div id="mode" class="badge"></div><section><div id="count" class="metric"></div><div>proposed site locations</div><p id="totals" class="small"></p></section>
<button id="fit">Fit country</button><input id="search" style="width:100%;margin-top:8px" placeholder="Find grid or site ID"><button id="find" style="margin-top:5px">Find</button>
<section><strong>Layers</strong><label><input type="checkbox" id="no_build_polygons" checked> No-build polygons</label><label><input type="checkbox" id="buildings" checked> Mapped building footprints (up to 5,000)</label><label><input type="checkbox" id="grids" checked> Complete grid footprints / KPI categories</label><label><input type="checkbox" id="airtel" checked> Airtel sites</label><label><input type="checkbox" id="competitors"> Competitor sites</label><label><input type="checkbox" id="sites" checked> Proposed sites</label><label><input type="checkbox" id="demand_points" checked> Supplied detailed demand points</label><label><input type="checkbox" id="radius"> Assumed service radius</label></section>
<div class="legend small"><div><span style="background:#d52f42"></span>Red</div><div><span style="background:#e3a21b"></span>Amber</div><div><span style="background:#2b9d70"></span>Green</div><div>▲ Airtel · ◆ Proposed · + Competitor · ● Detailed demand</div></div>
<section class="small">Drag to pan. Scroll to zoom. Hover for details. Click a proposed site below to locate it. At country scale, cells display as points. No online basemap.</section>
<div class="small" id="notes"></div><section><strong>Selection detail</strong><div id="detail">Hover over a map feature.</div></section><section><strong>Ranked sites</strong><div id="siteList"></div></section></aside>
<div id="mapwrap"><canvas id="map"></canvas><div id="tip"></div><div id="scale"></div></div></main>
<script>const D=__PAYLOAD__;
const $=id=>document.getElementById(id), cv=$('map'),ctx=cv.getContext('2d'),wrap=$('mapwrap');let W,H,z=1,cx=0,cy=0,drag=null,hits=[];
function project(lat,lon){let la=Math.max(-85,Math.min(85,lat))*Math.PI/180;return [lon*Math.PI/180,Math.log(Math.tan(Math.PI/4+la/2))]}
for(const key of ['grids','sites','airtel','competitors','demand_points'])D[key].forEach(p=>p.xy=project(p.lat,p.lon));
for(const f of [...D.building_footprints,...D.actual_grid_footprints,...D.no_build_polygons]){const polygons=f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.coordinates;f.xy=polygons.map(poly=>poly.map(ring=>ring.map(c=>project(c[1],c[0]))))}
function screen(p){return [W/2+(p[0]-cx)*z,H/2-(p[1]-cy)*z]}
function text(id,value){$(id).textContent=value}
text('subtitle',D.summary.country+' · '+D.summary.grid_size+' · '+D.summary.grid_period+' · '+D.summary.location_mode);
text('mode',D.summary.mode==='MR preview'?'MR PREVIEW — no population data':'Population planning');
text('count',(D.summary.proposed_sites??D.sites.length).toLocaleString());text('totals',D.summary.incremental_demand_total.toLocaleString(undefined,{maximumFractionDigits:0})+' '+D.summary.demand_unit+' assigned across '+D.summary.assigned_grids+' grids.');
text('notes',D.summary.notes.join('\n')+' Supplied polygons retain their boundaries. CSV-only footprints are nominal cells reconstructed from source centroids and size, not recovered survey boundaries.');
function fit(){let b=D.summary.grid_bounds;let pts=b?[project(b[1],b[0]),project(b[3],b[2])]:D.grids.map(p=>p.xy);if(!pts.length)return;let xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);let xmin=xs.reduce((a,b)=>Math.min(a,b),Infinity),xmax=xs.reduce((a,b)=>Math.max(a,b),-Infinity),ymin=ys.reduce((a,b)=>Math.min(a,b),Infinity),ymax=ys.reduce((a,b)=>Math.max(a,b),-Infinity);cx=(xmin+xmax)/2;cy=(ymin+ymax)/2;z=Math.min(W/Math.max(.001,xmax-xmin),H/Math.max(.001,ymax-ymin))*.88;draw()}
function draw(){scheduleViewport();ctx.clearRect(0,0,W,H);hits=[];ctx.strokeStyle='#dae1eb';ctx.lineWidth=1;let step=z>10000?.01:z>1000?.1:1;let left=cx-W/2/z,right=cx+W/2/z,bottom=cy-H/2/z,top=cy+H/2/z;
for(let x=Math.floor(left/step)*step;x<right;x+=step){let sx=screen([x,0])[0];ctx.beginPath();ctx.moveTo(sx,0);ctx.lineTo(sx,H);ctx.stroke()}
for(let y=Math.floor(bottom/step)*step;y<top;y+=step){let sy=screen([0,y])[1];ctx.beginPath();ctx.moveTo(0,sy);ctx.lineTo(W,sy);ctx.stroke()}
function drawPolygons(features,fill,stroke){for(const f of features){ctx.beginPath();for(const poly of f.xy)for(const ring of poly){ring.forEach((p,i)=>{let[x,y]=screen(p);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.closePath()}ctx.fillStyle=typeof fill==='function'?fill(f):fill;ctx.fill('evenodd');ctx.strokeStyle=stroke;ctx.lineWidth=.5;ctx.stroke()}}
if($('grids').checked&&D.actual_grid_footprints.length)drawPolygons(D.actual_grid_footprints,f=>({Red:'#d52f4260',Amber:'#e3a21b60',Green:'#2b9d7060',Grey:'#a1a8b160'})[f.properties.category]||'#a1a8b160','#7c8792');
if($('no_build_polygons').checked)drawPolygons(D.no_build_polygons,'#e8303940','#b72332');
if($('grids').checked) for(const p of D.grids){let [x,y]=screen(p.xy);if(x<-20||x>W+20||y<-20||y>H+20)continue;let side=(D.rules.grid_size==='1km'?1:3)/6371.0088/Math.cos(p.lat*Math.PI/180)*z;ctx.fillStyle=({Red:'#d52f42',Amber:'#e3a21b',Green:'#2b9d70',Grey:'#a1a8b1'})[p.category]||'#a1a8b1';ctx.globalAlpha=.55;if(side>5&&!D.actual_grid_footprints.length){ctx.fillRect(x-side/2,y-side/2,side,side);if(p.assigned_candidate){ctx.strokeStyle='#202b44';ctx.strokeRect(x-side/2,y-side/2,side,side)}}else if(!D.actual_grid_footprints.length){ctx.fillRect(x-1.4,y-1.4,2.8,2.8)}hits.push([x,y,p,'Grid'])}
ctx.globalAlpha=1;
if($('buildings').checked)for(const f of D.building_footprints){ctx.beginPath();for(const poly of f.xy)for(const ring of poly){ring.forEach((p,i)=>{let[x,y]=screen(p);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.closePath()}ctx.fillStyle='#76523866';ctx.fill('evenodd');ctx.strokeStyle='#65452d';ctx.lineWidth=.6;ctx.stroke()}
for(const key of ['competitors','demand_points','airtel','sites']){if(!$(key).checked)continue;for(const p of D[key]){let[x,y]=screen(p.xy);if(x<-20||x>W+20||y<-20||y>H+20)continue;
if(key==='sites'&&$('radius').checked){let r=D.rules.service_radius_km/6371.0088/Math.cos(p.lat*Math.PI/180)*z;ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle='#5269cb14';ctx.fill();ctx.strokeStyle='#5269cb70';ctx.stroke()}
ctx.beginPath();if(key==='sites'){ctx.moveTo(x,y-7);ctx.lineTo(x+6,y);ctx.lineTo(x,y+7);ctx.lineTo(x-6,y);ctx.closePath();ctx.fillStyle='#4920aa';ctx.fill();ctx.strokeStyle='white';ctx.stroke();if(z>12000){ctx.fillStyle='#251744';ctx.font='11px system-ui';ctx.fillText(p.rank,x+8,y+3)}}else if(key==='demand_points'){ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle='#0087a0';ctx.fill()}else if(key==='airtel'){ctx.moveTo(x,y-5);ctx.lineTo(x+4,y+4);ctx.lineTo(x-4,y+4);ctx.closePath();ctx.fillStyle=p.planning_role==='Active 4G/5G'?'#153d80':p.planning_role==='Reserved pipeline'?'#ce44a7':'#7b8492';ctx.fill()}else{ctx.moveTo(x-3,y);ctx.lineTo(x+3,y);ctx.moveTo(x,y-3);ctx.lineTo(x,y+3);ctx.strokeStyle='#444';ctx.stroke()}hits.push([x,y,p,key])}}
let lat=(2*Math.atan(Math.exp(cy))-Math.PI/2),km=100/z*6371.0088*Math.cos(lat);text('scale','100 px ≈ '+km.toFixed(km<1?2:1)+' km · WGS84 overview')}
let viewportTimer=null,viewportKey='',viewportVersion=0;
function preparePolygon(f){const polygons=f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.coordinates;f.xy=polygons.map(poly=>poly.map(ring=>ring.map(c=>project(c[1],c[0]))));return f}
function scheduleViewport(){
 if(!D.summary.database_path||!location.protocol.startsWith('http')||!W||!z)return;
 const invY=y=>(2*Math.atan(Math.exp(y))-Math.PI/2)*180/Math.PI;
 const bbox=[Math.max(-180,(cx-W/2/z)*180/Math.PI),Math.max(-85,invY(cy-H/2/z)),Math.min(180,(cx+W/2/z)*180/Math.PI),Math.min(85,invY(cy+H/2/z))];
 if(bbox[0]>=bbox[2]||bbox[1]>=bbox[3])return;
 const key=bbox.map(v=>v.toFixed(6)).join(',')+['grids','sites','airtel','competitors','no_build_polygons'].map(k=>$(k).checked).join('');
 if(key===viewportKey)return;viewportKey=key;const version=++viewportVersion;clearTimeout(viewportTimer);
 viewportTimer=setTimeout(async()=>{
  const layers=[['grids','grids'],['sites','sites'],['airtel','airtel'],['competitors','competitors'],['no_build_polygons','no_build']];let capped=false;
  try{
   for(const [control,layer] of layers){
    if(!$(control).checked)continue;
    const response=await fetch('/api/planning?layer='+layer+'&bbox='+encodeURIComponent(bbox.join(',')));
    if(!response.ok)throw Error('Local result session unavailable');
    const data=await response.json();if(version!==viewportVersion)return;capped||=data.truncated;
    if(layer==='grids'){D.actual_grid_footprints=data.features.filter(f=>f.geometry.type!=='Point').map(preparePolygon);D.grids=data.features.map(f=>({...f.properties,xy:project(f.properties.lat,f.properties.lon)}));}
    else if(layer==='no_build')D.no_build_polygons=data.features.map(preparePolygon);
    else D[layer]=data.features.map(f=>({...f.properties,xy:project(f.geometry.coordinates[1],f.geometry.coordinates[0])}));
   }
   text('viewportStatus',capped?' View capped at 5,000 features per layer. Zoom in for the remaining cells.':' All features in this viewport loaded from local disk.');draw();
  }catch(e){text('viewportStatus',' Showing the bounded saved preview. Reopen from the running desktop app for all areas.');}
 },180);
}
function resize(){let r=wrap.getBoundingClientRect();W=r.width;H=r.height;let d=window.devicePixelRatio||1;cv.width=W*d;cv.height=H*d;cv.style.width=W+'px';cv.style.height=H+'px';ctx.setTransform(d,0,0,d,0,0);draw()}
function detail(p,kind){return kind+'\n'+Object.entries(p).filter(([k])=>k!=='xy').map(([k,v])=>k+': '+(v===null?'Unavailable':v)).join('\n')}
function locate(p){cx=p.xy[0];cy=p.xy[1];z=Math.max(z,70000);text('detail',detail(p,'Selected'));draw()}
cv.addEventListener('pointerdown',e=>{drag={x:e.offsetX,y:e.offsetY,cx,cy};cv.setPointerCapture(e.pointerId);$('tip').style.display='none'});
cv.addEventListener('pointerup',()=>drag=null);cv.addEventListener('pointercancel',()=>drag=null);
cv.addEventListener('pointermove',e=>{if(drag){cx=drag.cx-(e.offsetX-drag.x)/z;cy=drag.cy+(e.offsetY-drag.y)/z;draw();return}let best=null,dist=10;for(let i=hits.length-1;i>=0;i--){let h=hits[i],d=Math.hypot(h[0]-e.offsetX,h[1]-e.offsetY);if(d<dist){dist=d;best=h}}if(best){text('detail',detail(best[2],best[3]));text('tip',(best[2].candidate_id||best[2].id||best[2].point_id)+' · '+(best[2].category||best[2].operator||best[2].planning_role||''));$('tip').style.display='block';$('tip').style.left=Math.min(W-220,e.offsetX+12)+'px';$('tip').style.top=Math.max(0,e.offsetY-30)+'px'}else $('tip').style.display='none'});
cv.addEventListener('wheel',e=>{e.preventDefault();let px=cx+(e.offsetX-W/2)/z,py=cy-(e.offsetY-H/2)/z;z=Math.max(50,Math.min(2e7,z*Math.exp(-e.deltaY*.0015)));cx=px-(e.offsetX-W/2)/z;cy=py+(e.offsetY-H/2)/z;draw()},{passive:false});
cv.addEventListener('pointerleave',()=>{$('tip').style.display='none'});
for(const id of ['grids','airtel','competitors','sites','demand_points','radius','buildings','no_build_polygons'])$(id).onchange=draw;$('fit').onclick=fit;
$('find').onclick=()=>{let id=$('search').value.trim().toLowerCase();let p=[...D.sites,...D.grids,...D.airtel,...D.competitors].find(p=>(p.candidate_id||p.id||'').toLowerCase()===id);if(p)locate(p);else text('detail','No matching ID in this run.')};
for(const p of D.sites){let b=document.createElement('button');b.textContent=p.rank+'. '+p.candidate_id+' · '+Math.round(p.incremental_demand).toLocaleString();b.onclick=()=>locate(p);$('siteList').appendChild(b)}
window.addEventListener('resize',resize);resize();fit();
</script></body></html>'''
