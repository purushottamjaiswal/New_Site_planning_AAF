"""Shared map controls and spherical geometry, embedded for offline use."""
CONTROLS = r'''<section aria-label="Map tools"><strong>Map tools</strong>
<label><input type="checkbox" id="toolRadius" checked> Tentative assumed service radius: <span id="toolRadiusValue"></span></label>
<div class="small">Planning assumption around proposed sites; not predicted RF coverage.</div>
<label for="toolSearch">Grid / site ID</label><input id="toolSearch" style="width:100%" placeholder="Enter exact grid or site ID">
<select id="toolSearchLayer" aria-label="Search layer"><option value="all">All grids and sites</option><option value="grids">Grid ID</option><option value="sites">Proposed site ID</option><option value="airtel">Airtel site ID</option><option value="competitors">Competitor site ID</option></select>
<button id="toolFind">Find and fly to location</button><div id="toolSearchStatus" class="small" role="status"></div><div id="toolSearchResults"></div>
<p><button id="toolMeasure" aria-pressed="false">Start measuring</button> <button id="toolUndo">Undo point</button> <button id="toolClear">Clear measurement</button></p>
<div id="toolDistance" role="status">Distance: 0 m</div><div class="small">Start measuring, then click successive points on the map. Click Finish measuring to resume normal selection. Distance follows straight great-circle segments, not road routes.</div></section>'''

SCRIPT = r'''
const MapTools=(()=>{
 const earth=6371008.8,rad=Math.PI/180;
 function distance(a,b){const p=a[1]*rad,q=b[1]*rad,dp=q-p,dl=(b[0]-a[0])*rad;const h=Math.sin(dp/2)**2+Math.cos(p)*Math.cos(q)*Math.sin(dl/2)**2;return 2*earth*Math.atan2(Math.sqrt(Math.min(1,h)),Math.sqrt(Math.max(0,1-h)))}
 function length(points){return points.slice(1).reduce((sum,p,i)=>sum+distance(points[i],p),0)}
 function ring(center,metres,steps=48){const p=center[1]*rad,l=center[0]*rad,d=metres/earth,out=[];for(let i=0;i<=steps;i++){const b=i*2*Math.PI/steps;const q=Math.asin(Math.sin(p)*Math.cos(d)+Math.cos(p)*Math.sin(d)*Math.cos(b));const m=l+Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(p),Math.cos(d)-Math.sin(p)*Math.sin(q));out.push([m/rad,q/rad])}return out}
 function center(f){const p=f.properties||{};return Number.isFinite(p.lon)&&Number.isFinite(p.lat)?[p.lon,p.lat]:f.geometry.type==='Point'?f.geometry.coordinates:null}
 function install({radiusKm,features,fly,redraw,live}){
  const el=id=>document.getElementById(id);const state={measuring:false,points:[],radius:true,selected:null};let request=0;
  el('toolRadiusValue').textContent=radiusKm+' km';
  const refresh=()=>{const m=length(state.points);el('toolDistance').textContent='Distance: '+(m<1000?m.toFixed(1)+' m':(m/1000).toFixed(3)+' km')+' · '+state.points.length+' points';el('toolMeasure').textContent=state.measuring?'Finish measuring':'Start measuring';el('toolMeasure').setAttribute('aria-pressed',String(state.measuring));redraw(state)};
  el('toolRadius').onchange=()=>{state.radius=el('toolRadius').checked;refresh()};
  el('toolMeasure').onclick=()=>{state.measuring=!state.measuring;refresh()};
  el('toolUndo').onclick=()=>{state.points.pop();refresh()};el('toolClear').onclick=()=>{state.points=[];refresh()};
  const choose=f=>{state.selected=f;fly(f);refresh()};
  async function find(){const id=el('toolSearch').value.trim(),layer=el('toolSearchLayer').value,version=++request;el('toolSearchResults').replaceChildren();if(!id){el('toolSearchStatus').textContent='Enter an exact ID.';return}el('toolSearchStatus').textContent='Searching…';let matches=[],full=false;
   if(live&&location.protocol.startsWith('http')){try{const response=await fetch('/api/search?id='+encodeURIComponent(id)+'&layer='+encodeURIComponent(layer));if(!response.ok)throw Error('Unavailable');const data=await response.json();if(version!==request)return;matches=data.features;full=true}catch(e){/* Saved preview remains available after a session closes. */}}
   if(!full)matches=features().filter(f=>(layer==='all'||f.properties.layer===layer)&&String(f.properties.candidate_id||f.properties.id||'').toLowerCase()===id.toLowerCase()).slice(0,20);
   if(version!==request)return;el('toolSearchStatus').textContent=matches.length?(matches.length+' match(es). '+(full?'Searched all run results.':'Searched saved/loaded preview only; keep the desktop app open to search all results.')):(full?'No matching ID in this run.':'No match in saved/loaded preview. Open from the running desktop app to search all results.');
   for(const f of matches){const button=document.createElement('button');button.style.cssText='display:block;width:100%;text-align:left;margin:4px 0';button.textContent=f.properties.layer+': '+(f.properties.candidate_id||f.properties.id);button.onclick=()=>choose(f);el('toolSearchResults').appendChild(button)}if(matches.length===1)choose(matches[0]);
  }
  el('toolFind').onclick=find;el('toolSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();find()}});
  return {state,refresh,choose,add:xy=>{if(!state.measuring)return false;state.points.push(xy);refresh();return true}};
 }
 return {distance,length,ring,center,install};
})();
'''

def embed_tools(template):
    return template.replace('__MAP_TOOLS_HTML__',CONTROLS).replace('__MAP_TOOLS_JS__',SCRIPT)
