"""Disk-backed global ID lookup and shared map geometry/interaction regression checks."""
import sys,json,tempfile,unittest,urllib.request,urllib.error,subprocess,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from session_db import Session
from web_maps import LocalMapServer,map_data
from map_tools import SCRIPT,embed_tools
from planner import Rules

class MapToolTests(unittest.TestCase):
    def test_search_finds_site_beyond_preview_and_handles_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);session=Session();server=None
            try:
                for i in range(10050):session.add('sites',{'candidate_id':f'NG_NEW_{i+1}','lat':7.,'lon':3.,'rank':i+1})
                session.add('grids',{'id':'NG_NEW_10050','lat':7.,'lon':3.},json.dumps({'type':'Polygon','coordinates':[[[2.99,6.99],[3.01,6.99],[3.01,7.01],[2.99,6.99]]]}));session.db.commit()
                (root/'run_settings.json').write_text(json.dumps({'temporary_database':str(session.path)}),encoding='utf-8');server=LocalMapServer(root)
                def search(value,layer='all'):
                    from urllib.parse import urlencode
                    with urllib.request.urlopen('http://127.0.0.1:8765/api/search?'+urlencode({'id':value,'layer':layer})) as response:return json.load(response)
                result=search('ng_new_10050');self.assertEqual(len(result['features']),2)
                self.assertEqual([f['properties']['layer'] for f in result['features']],['sites','grids'])
                self.assertEqual(search('NG_NEW_10050','sites')['features'][0]['properties']['rank'],10050)
                self.assertEqual(search("' OR 1=1 --")['features'],[])
                self.assertEqual(search('absent')['features'],[])
                with self.assertRaises(urllib.error.HTTPError) as error:search('id','inputs')
                self.assertEqual(error.exception.code,400)
                plan=session.db.execute('EXPLAIN QUERY PLAN SELECT payload FROM sites WHERE gid=? COLLATE NOCASE',('id',)).fetchall()
                self.assertIn('sites_search_id',str(plan))
                session.close()
                with self.assertRaises(urllib.error.HTTPError) as error:search('NG_NEW_10050')
                self.assertEqual(error.exception.code,503)
            finally:
                if server:server.close()
                session.close()

    def test_generated_viewers_include_controls_and_offline_map_interactions(self):
        import re
        from planner import plan_frames
        from test_planner import fixtures
        from map_report import write_map
        from openlayers_map import write_openlayers_map
        from web_maps import write_web_map
        node=shutil.which('node')
        if not node:self.skipTest('Node required for generated map checks')
        plan=plan_frames(fixtures(),Rules(location_mode='Quick demo (grid centroid)'),log=lambda s:None)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name,writer in [('offline',write_map),('openlayers',write_openlayers_map),('alternate',write_web_map)]:
                html=root/(name+'.html');writer(plan,html);text=html.read_text(encoding='utf-8')
                self.assertEqual(text.count('id="toolRadius"'),1)
                self.assertEqual(text.count('id="toolFind"'),1)
                self.assertNotIn('__MAP_TOOLS_',text)
                js=root/(name+'.js');js.write_text('\n'.join(re.findall(r'<script>(.*?)</script>',text,re.S)),encoding='utf-8')
                result=subprocess.run([node,'--check',str(js)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([node,str(Path(__file__).parent/'offline_map_tools_smoke.cjs'),str(root/'offline.html')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_shared_tools_geometry_and_search_interactions_in_javascript(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required for shared JavaScript runtime checks')
        harness=r'''
const assert=require('node:assert/strict');
const elements={};function element(id){return elements[id]??=( {value:'',checked:true,textContent:'',children:[],setAttribute(k,v){this[k]=v},replaceChildren(){this.children=[]},appendChild(c){this.children.push(c)},addEventListener(k,f){this[k]=f},style:{}})}
global.document={getElementById:element,createElement:()=>({style:{}})};global.location={protocol:'http:'};
'''
        checks=r'''
assert.ok(Math.abs(MapTools.distance([0,0],[1,0])-111195.0802)<.1);
for(const center of [[3.7,7.4],[3,70],[-179.99,1]])for(const p of MapTools.ring(center,1500))assert.ok(Math.abs(MapTools.distance(center,p)-1500)<.00001);
let redraw=0,chosen=null;const f={properties:{layer:'sites',candidate_id:'NG_NEW_10050'},geometry:{type:'Point',coordinates:[3,7]}};
const tools=MapTools.install({radiusKm:1.5,live:true,features:()=>[],fly:x=>chosen=x,redraw:()=>redraw++});
element('toolMeasure').onclick();assert.equal(tools.add([0,0]),true);tools.add([1,0]);assert.match(element('toolDistance').textContent,/111.195 km/);element('toolMeasure').onclick();assert.equal(tools.add([2,0]),false);element('toolUndo').onclick();assert.equal(tools.state.points.length,1);element('toolClear').onclick();assert.equal(tools.state.points.length,0);
element('toolRadius').checked=false;element('toolRadius').onchange();assert.equal(tools.state.radius,false);
element('toolSearch').value='NG_NEW_10050';element('toolSearchLayer').value='all';global.fetch=async url=>({ok:true,json:async()=>({features:[f]})});
(async()=>{await element('toolFind').onclick();assert.equal(chosen,f);assert.match(element('toolSearchStatus').textContent,/all run results/);assert.equal(element('toolSearchResults').children.length,1);global.fetch=async()=>{throw Error('closed')};await element('toolFind').onclick();assert.match(element('toolSearchStatus').textContent,/saved\/loaded preview/);assert.ok(redraw>4);console.log('Shared map tools geometry and interactions passed')})().catch(e=>{console.error(e);process.exit(1)});
'''
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'tools.cjs';path.write_text(harness+SCRIPT+checks,encoding='utf-8')
            result=subprocess.run([node,str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
