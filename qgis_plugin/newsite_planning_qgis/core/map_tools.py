"""Shared map controls and spherical geometry, embedded for offline use."""
CONTROLS = r'''<section aria-label="Map tools"><strong>Map tools</strong>
<label><input type="checkbox" id="toolRadius" checked> Tentative assumed service radius: <span id="toolRadiusValue"></span></label>
<div class="small">Planning assumption around proposed sites; not predicted RF coverage.</div>
<label for="toolSearch">Grid / site ID</label><input id="toolSearch" style="width:100%" placeholder="Enter exact grid or site ID">
<select id="toolSearchLayer" aria-label="Search layer"><option value="all">All grids and sites</option><option value="grids">Grid ID</option><option value="sites">Proposed site ID</option><option value="airtel">Airtel site ID</option><option value="competitors">Competitor site ID</option></select>
<button id="toolFind">Find and fly to location</button><div id="toolSearchStatus" class="small" role="status"></div><div id="toolSearchResults"></div>
<p><button id="toolMeasure" aria-pressed="false">Start measuring</button> <button id="toolUndo">Undo point</button> <button id="toolClear">Clear measurement</button></p>
<div id="toolDistance" role="status">Distance: 0 m</div><div class="small">Start measuring, then click successive points on the map. Click Finish measuring to resume normal selection. Distance follows straight great-circle segments, not road routes.</div></section><section><strong>Draft candidate review</strong><div class="small">Select a proposed site to move it, or leave Site ID blank for a new draft. Checks compare against the other fixed planned sites. Original plan remains unchanged.</div>
<label>Original site ID <input id="reviewSite" style="width:100%" placeholder="Optional: existing proposed site ID"></label>
<label>Draft latitude <input id="reviewLat" type="number" step="any" style="width:100%"></label><label>Draft longitude <input id="reviewLon" type="number" step="any" style="width:100%"></label>
<button id="reviewPick">Pick draft location on map</button><button id="reviewCheck">Check draft / compare demand</button>
<label>Reviewer <input id="reviewer" style="width:100%"></label><label>Review status <select id="reviewStatus"><option>Imagery reviewed</option><option>Field verified</option><option>Acquisition approved</option><option>Rejected</option></select></label>
<label>Evidence / notes <textarea id="reviewNotes" style="width:100%" rows="3"></textarea></label><button id="reviewSave">Save draft review</button><button id="reviewHistory">Load review history</button><button id="reviewAlternatives">Show this site's alternatives</button><div id="reviewAlternativeList"></div>
<pre id="reviewResult" class="small" style="white-space:pre-wrap;overflow-wrap:anywhere" role="status"></pre><a href="reviewed_candidates.csv" download>Download review history / candidate CSV</a>
<div class="small">Saved drafts do not change site assignments. Reviewed CSV rows default to feasible=No; verify and explicitly approve feasibility before replanning as supplied candidates.</div></section>'''

SCRIPT = r'''
const MapTools=(()=>{
 const earth=6371008.8,rad=Math.PI/180;
 function distance(a,b){const p=a[1]*rad,q=b[1]*rad,dp=q-p,dl=(b[0]-a[0])*rad;const h=Math.sin(dp/2)**2+Math.cos(p)*Math.cos(q)*Math.sin(dl/2)**2;return 2*earth*Math.atan2(Math.sqrt(Math.min(1,h)),Math.sqrt(Math.max(0,1-h)))}
 function length(points){return points.slice(1).reduce((sum,p,i)=>sum+distance(points[i],p),0)}
 function ring(center,metres,steps=48){const p=center[1]*rad,l=center[0]*rad,d=metres/earth,out=[];for(let i=0;i<=steps;i++){const b=i*2*Math.PI/steps;const q=Math.asin(Math.sin(p)*Math.cos(d)+Math.cos(p)*Math.sin(d)*Math.cos(b));const m=l+Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(p),Math.cos(d)-Math.sin(p)*Math.sin(q));out.push([m/rad,q/rad])}return out}
 function center(f){const p=f.properties||{};return Number.isFinite(p.lon)&&Number.isFinite(p.lat)?[p.lon,p.lat]:f.geometry.type==='Point'?f.geometry.coordinates:null}
 function install({radiusKm,features,fly,redraw,live}){
  const el=id=>document.getElementById(id);const state={measuring:false,points:[],radius:true,selected:null,draft:null,picking:false,alternatives:[]};let request=0;
  el('toolRadiusValue').textContent=radiusKm+' km';
  const refresh=()=>{const m=length(state.points);el('toolDistance').textContent='Distance: '+(m<1000?m.toFixed(1)+' m':(m/1000).toFixed(3)+' km')+' · '+state.points.length+' points';el('toolMeasure').textContent=state.measuring?'Finish measuring':'Start measuring';el('toolMeasure').setAttribute('aria-pressed',String(state.measuring));redraw(state)};
  el('toolRadius').onchange=()=>{state.radius=el('toolRadius').checked;refresh()};
  el('toolMeasure').onclick=()=>{state.measuring=!state.measuring;refresh()};
  el('toolUndo').onclick=()=>{state.points.pop();refresh()};el('toolClear').onclick=()=>{state.points=[];refresh()};
  const select=f=>{state.selected=f;const xy=center(f);if(xy){el('reviewLat').value=xy[1];el('reviewLon').value=xy[0]}el('reviewSite').value=f.properties.layer==='sites'?f.properties.candidate_id:'';refresh()};
  const choose=f=>{select(f);fly(f);refresh()};
  el('reviewPick').onclick=()=>{state.picking=!state.picking;state.measuring=false;el('reviewPick').textContent=state.picking?'Click the map for the draft point':'Pick draft location on map';refresh()};
  async function review(save){try{const payload={site_id:el('reviewSite').value.trim(),lat:Number(el('reviewLat').value),lon:Number(el('reviewLon').value),reviewer:el('reviewer').value,status:el('reviewStatus').value||'Imagery reviewed',notes:el('reviewNotes').value};if(!el('reviewLat').value||!el('reviewLon').value)throw Error('Enter or pick draft coordinates.');const response=await fetch('/api/review/'+(save?'save':'preview'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw Error(result.error||'Persistent project review unavailable. Re-export with project persistence enabled.');el('reviewResult').textContent=JSON.stringify(result,null,2);state.draft=[payload.lon,payload.lat];refresh()}catch(e){el('reviewResult').textContent=e.message}}
  el('reviewAlternatives').onclick=async()=>{try{const response=await fetch('/api/alternatives?site_id='+encodeURIComponent(el('reviewSite').value));if(!response.ok)throw Error('Select a proposed site from an updated exported result.');const data=await response.json();state.alternatives=data.features;el('reviewAlternativeList').replaceChildren();for(const f of data.features){const button=document.createElement('button');button.textContent=f.properties.source_candidate_id+' · benefit '+Number(f.properties.weighted_benefit).toFixed(1);button.onclick=()=>{state.draft=center(f);el('reviewLat').value=state.draft[1];el('reviewLon').value=state.draft[0];fly(f);refresh()};el('reviewAlternativeList').appendChild(button)}el('reviewResult').textContent=data.features.length?'Local alternatives scored at original selection time. Recheck a draft before saving.':'No other same-grid alternatives were available at selection time.';refresh()}catch(e){el('reviewResult').textContent=e.message}};
  el('reviewCheck').onclick=()=>review(false);el('reviewSave').onclick=()=>review(true);el('reviewHistory').onclick=async()=>{try{const response=await fetch('/api/reviews?site_id='+encodeURIComponent(el('reviewSite').value));if(!response.ok)throw Error('Review history unavailable');el('reviewResult').textContent=JSON.stringify(await response.json(),null,2)}catch(e){el('reviewResult').textContent=e.message}};
  async function find(){const id=el('toolSearch').value.trim(),layer=el('toolSearchLayer').value,version=++request;el('toolSearchResults').replaceChildren();if(!id){el('toolSearchStatus').textContent='Enter an exact ID.';return}el('toolSearchStatus').textContent='Searching…';let matches=[],full=false;
   if(live&&location.protocol.startsWith('http')){try{const response=await fetch('/api/search?id='+encodeURIComponent(id)+'&layer='+encodeURIComponent(layer));if(!response.ok)throw Error('Unavailable');const data=await response.json();if(version!==request)return;matches=data.features;full=true}catch(e){/* Saved preview remains available after a session closes. */}}
   if(!full)matches=features().filter(f=>(layer==='all'||f.properties.layer===layer)&&String(f.properties.candidate_id||f.properties.id||'').toLowerCase()===id.toLowerCase()).slice(0,20);
   if(version!==request)return;el('toolSearchStatus').textContent=matches.length?(matches.length+' match(es). '+(full?'Searched all run results.':'Searched saved/loaded preview only; keep the desktop app open to search all results.')):(full?'No matching ID in this run.':'No match in saved/loaded preview. Open from the running desktop app to search all results.');
   for(const f of matches){const button=document.createElement('button');button.style.cssText='display:block;width:100%;text-align:left;margin:4px 0';button.textContent=f.properties.layer+': '+(f.properties.candidate_id||f.properties.id);button.onclick=()=>choose(f);el('toolSearchResults').appendChild(button)}if(matches.length===1)choose(matches[0]);
  }
  el('toolFind').onclick=find;el('toolSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();find()}});
  return {state,refresh,choose,select,add:xy=>{if(state.picking){state.draft=xy;state.picking=false;el('reviewLat').value=xy[1];el('reviewLon').value=xy[0];el('reviewPick').textContent='Pick draft location on map';refresh();return true}if(!state.measuring)return false;state.points.push(xy);refresh();return true}};
 }
 return {distance,length,ring,center,install};
})();
'''

def embed_tools(template):
    return template.replace('__MAP_TOOLS_HTML__',CONTROLS).replace('__MAP_TOOLS_JS__',SCRIPT)
