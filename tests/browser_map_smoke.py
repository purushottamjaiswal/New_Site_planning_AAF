"""Real Chromium offline-map integration smoke; optional local tool, required in CI."""
import sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from planner import Rules,run_paths,save_plan
from test_planner import fixtures
from mask_fixtures import masks
from web_maps import LocalMapServer

def main():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);paths={}
        for role,frame in fixtures().items():
            p=root/(role+'.csv');frame.to_csv(p,index=False);paths[role]=str(p)
        paths.update(masks(root));result=run_paths(paths,Rules(location_mode='Area search'),log=lambda s:None)
        folder=save_plan(result,root/'out');site=result.sites.head().iloc[0].to_dict();result.close();server=LocalMapServer(folder)
        try:
            with sync_playwright() as runtime:
                browser=runtime.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(server.url.replace('planning_openlayers_map.html','planning_map.html'));page.wait_for_selector('#toolRadiusValue')
                page.locator('#toolSearch').fill(site['candidate_id']);page.locator('#toolFind').click();page.wait_for_function("document.getElementById('toolSearchStatus').textContent.includes('1 match')")
                assert page.locator('#reviewSite').input_value()==site['candidate_id']
                page.locator('#toolMeasure').click();canvas=page.locator('#map');canvas.click(position={'x':200,'y':200});canvas.click(position={'x':300,'y':250});assert '2 points' in page.locator('#toolDistance').inner_text();page.locator('#toolMeasure').click()
                page.locator('#reviewLat').fill(str(site['lat']));page.locator('#reviewLon').fill(str(site['lon']));page.locator('#reviewCheck').click();page.wait_for_function("document.getElementById('reviewResult').textContent.includes('checks_passed')")
                assert json.loads(page.locator('#reviewResult').inner_text())['checks_passed']
                page.locator('#reviewer').fill('CI reviewer');page.locator('#reviewNotes').fill('Synthetic browser integration test');page.locator('#reviewSave').click();page.wait_for_function("document.getElementById('reviewResult').textContent.includes('revision')")
                assert not errors,errors;browser.close();print('Chromium offline map: fly-to, measurement, draft preview and save passed.')
        finally:server.close()
if __name__=='__main__':main()
