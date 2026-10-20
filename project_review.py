"""Durable derived results and auditable draft reviews. Official plan is immutable."""
import json,sqlite3,math,os,csv,shutil
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
from shapely.geometry import Point,shape
from session_db import Session,distance,encode
from planner import Rules
from planning_policy import location_allowed
from terrain_masks import screen_water
from engineering import Engineering

STATUSES=('Imagery reviewed','Field verified','Acquisition approved','Rejected')

def save_project(session,folder,rules,paths):
    target=Path(folder)/'planning_project.sqlite';partial=target.with_suffix('.partial')
    building_index=None
    if session.buildings:
        building_index=Path(folder)/'building_index.sqlite';shutil.copy2(session.buildings.path,building_index)
    session.db.commit()
    with closing(sqlite3.connect(partial)) as db:
        session.db.backup(db);db.execute('DROP TABLE inputs')
        db.execute('CREATE TABLE project_settings(value TEXT)')
        db.execute('CREATE TABLE reviews(revision INTEGER PRIMARY KEY AUTOINCREMENT,site_id TEXT,record TEXT)')
        db.execute('CREATE INDEX review_site ON reviews(site_id,revision)')
        settings={'schema':1,'rules':vars(rules),'input_metadata':session.metadata,'terrain':paths.get('terrain'),'building_index':building_index.name if building_index else None}
        db.execute('INSERT INTO project_settings VALUES(?)',(encode(settings),));db.commit();db.execute('VACUUM')
    os.replace(partial,target);return target

class ProjectSession(Session):
    def __init__(self,path):
        self.path=Path(path);self.db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True);self.batch_size=256;self.cancelled=lambda:False;self.log=lambda s:None;self.closed=False
        self.engineering=None;self.buildings=None
        try:
            self.settings=json.loads(self.db.execute('SELECT value FROM project_settings').fetchone()[0]);self.rules=Rules(**self.settings['rules']);self.buildings=None;self.engineering=None
            index=self.settings.get('building_index')
            if index and not Path(index).is_absolute():index=self.path.parent/index
            if index and Path(index).is_file():
                from building_index import DiskBuildingContext
                self.buildings=DiskBuildingContext(index)
            terrain=self.settings.get('terrain');self.missing_terrain=bool(terrain and not Path(terrain).is_file())
            from dataclasses import replace
            self.engineering=Engineering(self,replace(self.rules,terrain_required=False) if self.missing_terrain else self.rules,None if self.missing_terrain else terrain)
        except BaseException:
            if self.engineering:self.engineering.close()
            self.db.close();self.closed=True;raise

    def close(self):
        if not self.closed:
            if self.engineering:self.engineering.close()
            self.db.close();self.closed=True

def evaluate(project,request):
    lat=float(request['lat']);lon=float(request['lon'])
    if not(math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180):raise ValueError('Enter valid WGS84 coordinates.')
    site_id=str(request.get('site_id','')).strip();s=project;r=s.rules
    match=s.db.execute('SELECT payload FROM sites WHERE gid=?',(site_id,)).fetchone() if site_id else None
    if site_id and not match:raise ValueError('Select an existing proposed site, or leave Site ID blank for a new draft candidate.')
    original=json.loads(match[0]) if match else None;point=Point(lon,lat);parents=[]
    for payload,geometry in s.db.execute('SELECT t.payload,t.geometry FROM grids_rt b JOIN grids t ON b.rid=t.rid WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(lon,lon,lat,lat)):
        if geometry and shape(json.loads(geometry)).covers(point):parents.append(json.loads(payload))
    parents.sort(key=lambda g:(g['id']!=(original or {}).get('grid_id'),g['id']))
    errors=[];warnings=[];parent=next((p for p in parents if location_allowed(p,r)),None)
    if parent is None:errors.append('Outside permitted selected grid polygons')
    elif r.location_policy=='Current behaviour' and r.location_mode!='Supplied candidates' and not parent['target']:errors.append('Generated-location policy requires an eligible target grid')
    row={**(original or {}),'lat':lat,'lon':lon,'suitability':float((original or {}).get('suitability',1)),'building_factor':1.,'source_candidate_id':(original or {}).get('source_candidate_id','DRAFT_UNCALIBRATED'),'rejection':''}
    if original and (lat!=original['lat'] or lon!=original['lon']):
        if s.engineering.has_rf:warnings.append('Moving a site invalidates its original calibrated RF footprint; new RF evidence is required for that coordinate')
        row['source_candidate_id']='DRAFT_UNCALIBRATED'
    water=screen_water(s,row)
    if water:errors.append(water)
    d,_=s.nearest('airtel',lat,lon,"json_extract(t.payload,'$.blocking')=1")
    row['nearest_airtel_km']=d
    if d<r.min_airtel_km:errors.append('Too near active/reserved Airtel site')
    for _,other,dist,_ in s.spatial('sites',lat,lon,r.min_new_km,'t.gid<>?',(site_id,)):
        if dist<=r.min_new_km:errors.append('Too near proposed site '+other['candidate_id'])
    if parent and r.respect_site_present and parent['site_present'].lower() in ('yes','true','y','1'):errors.append('Parent grid has site-present exclusion')
    for geom, in s.db.execute('SELECT t.geometry FROM no_build_rt b JOIN no_build t ON b.rid=t.rid WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(lon,lon,lat,lat)):
        if shape(json.loads(geom)).covers(point):errors.append('Inside/on no-build polygon');break
    radius=s.db.execute("SELECT max(json_extract(payload,'$.radius_m')) FROM buffers").fetchone()[0] or 0
    for _,buffer,dist,_ in s.spatial('buffers',lat,lon,radius/1000):
        if dist<=buffer['radius_m']/1000:errors.append('Inside no-build buffer '+buffer['id'])
    for kind in ('road','backhaul','power'):row['nearest_'+kind+'_km']=s.nearest('infrastructure',lat,lon,"lower(json_extract(t.payload,'$.kind'))=?",(kind,))[0]
    errors.extend(s.engineering.screen(row))
    if s.buildings:
        import pandas as pd
        from dataclasses import replace
        frame=pd.DataFrame([row]);s.buildings.annotate(frame,replace(r,building_clearance_m=max(r.building_clearance_m,r.site_compound_radius_m)));row.update(frame.iloc[0].to_dict())
        if row['rejection']:errors.append(row['rejection'])
    elif s.settings['input_metadata'].get('buildings'):warnings.append('Building index unavailable; building checks must be repeated before engineering approval')
    if s.missing_terrain:warnings.append('Original DEM unavailable; terrain check must be repeated')
    opportunity=benefit=0.;points=0
    for _,d,dist,_ in s.spatial('demand',lat,lon,r.service_radius_km,"(t.assigned='' OR t.assigned=?)",(site_id,)):
        if not s.engineering.rf_allows(row,d):continue
        opportunity+=d['demand'];benefit+=d['weight']*math.exp(-r.distance_decay*(dist/r.service_radius_km)**2)*row['suitability']*row['building_factor'];points+=1
    prior=float((original or {}).get('incremental_demand',0))
    return {'site_id':site_id,'grid_id':parent['id'] if parent else None,'lat':lat,'lon':lon,'checks_passed':not errors,'errors':list(dict.fromkeys(errors)),'warnings':warnings,'incremental_demand_if_moved':opportunity,'change_from_original_site_demand':opportunity-prior,'weighted_benefit':benefit,'assigned_points_if_moved':points,'original_coordinates':{'lat':original['lat'],'lon':original['lon']} if original else None,'interpretation':'Draft comparison against other fixed selected sites. Saving does not alter the official plan or its assignments. Replan from reviewed candidates for a new official result.'}

def save_review(project,request):
    report=evaluate(project,request);reviewer=str(request.get('reviewer','')).strip();notes=str(request.get('notes','')).strip();status=request.get('status','Imagery reviewed')
    if not reviewer or not notes:raise ValueError('Reviewer and evidence/notes are required.')
    if status not in STATUSES:raise ValueError('Choose a valid review status.')
    if status!='Rejected' and not report['checks_passed']:raise ValueError('Resolve failed checks before saving a positive review, or mark this draft Rejected.')
    if status in ('Field verified','Acquisition approved') and report['warnings']:raise ValueError('Restore missing engineering evidence before recording this approval.')
    record={**report,'reviewer':reviewer[:200],'notes':notes[:4000],'review_status':status,'reviewed_utc':datetime.now(timezone.utc).isoformat()};key=report['site_id'] or 'NEW_DRAFT'
    cur=project.db.execute('INSERT INTO reviews(site_id,record) VALUES(?,?)',(key,encode(record)));project.db.commit();record['revision']=cur.lastrowid
    folder=project.path.parent;tmp=folder/'reviewed_candidates.csv.partial'
    with tmp.open('w',newline='',encoding='utf-8-sig') as f:
        fields=['revision','opco','candidate_id','grid_id','latitude','longitude','feasible','review_status','reviewer','reviewed_utc','notes'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for revision,text in project.db.execute('SELECT revision,record FROM reviews ORDER BY revision'):
            x=json.loads(text);w.writerow(dict(revision=revision,opco=project.rules.country,candidate_id=f'REVIEW_{revision}',grid_id=x['grid_id'],latitude=x['lat'],longitude=x['lon'],feasible='No',review_status=x['review_status'],reviewer=x['reviewer'],reviewed_utc=x['reviewed_utc'],notes=x['notes']))
    os.replace(tmp,folder/'reviewed_candidates.csv');return record
