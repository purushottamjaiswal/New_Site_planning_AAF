import sys,time,tempfile,csv,json,resource
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from planner import Rules,run_paths,save_plan
from mask_fixtures import masks
start=time.monotonic()
with tempfile.TemporaryDirectory(prefix='aaf_scale_check_') as td:
 root=Path(td);paths={}
 def write(role,headers,rows):
  p=root/(role+'.csv');paths[role]=str(p)
  with p.open('w',newline='') as f:
   w=csv.writer(f);w.writerow(headers);w.writerows(rows)
 count=10050
 write('grid',['opco','grid_id','centroid_lat','centroid_long','combined_category_wf','grid_size','month','total_mr_count','site_present'],(['NG',f'g{i:07}',(i//150)*.02,(i%150)*.02,'Red','1km',"Sep'26",100,'No'] for i in range(count)))
 write('airtel',['opco_id','physical_site_id','latitude','longitude','physical_site_status','technology','month'],[['NG','remote',-60,-120,'On-Air','4G','Sep_2026']])
 write('competitor',['OPCO_ID','SiteID','Latitude','Longitude','Technology','OPCO_Comp','DB_Period'],[['KE','foreign',-50,-150,'4G','Test',"May'26"]])
 def population():
  for i in range(count):yield ['NG',f'g{i:07}',1]
  for i in range(1000001):yield ['KE',f'foreign{i:08}',1]
 write('population',['opco','grid_id','population'],population())
 print('Fixture: 1,010,051 population rows; 10,050 target grids',flush=True)
 r=Rules(max_sites=12000,max_candidate_points=20000,location_mode='Grid centroid (legacy)',service_radius_km=.05,min_new_km=.1,min_airtel_km=.1,min_incremental=1,import_batch_size=2000)
 result=run_paths({**masks(root),**paths},r,log=lambda s:print(s,flush=True))
 try:
  assert len(result.sites)==10050,result.summary
  assert result.summary['incremental_demand_total']==10050
  assert len(result.site_page(10000,200))==50
  dbpath=result.session.path;bytes_db=dbpath.stat().st_size;input_rows=result.session.count('inputs')
  folder=save_plan(result,root/'outputs',paths)
  with (folder/'proposed_sites.csv').open(encoding='utf-8-sig') as f:exported=sum(1 for _ in csv.DictReader(f))
  assert exported==10050
  report={'fixture':'Synthetic source/selection stress trial; not operational network data','population_source_rows':1010051,'stored_input_rows':input_rows,'target_grids':10050,'requested_sites':12000,'selected_sites':len(result.sites),'full_exported_sites':exported,'page_offset_10000_rows':50,'assigned_demand':result.summary['incremental_demand_total'],'sqlite_bytes':bytes_db,'peak_process_rss_mib_linux':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),'elapsed_seconds_including_generation_export':round(time.monotonic()-start,2),'import_batch_rows':2000,'selection':'Global lazy greedy; no global link list or heap in RAM'}
 finally:result.close()
 report['temporary_database_removed']=not dbpath.exists();assert report['temporary_database_removed']
 Path(__file__).with_name('water_grid_scale_check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
