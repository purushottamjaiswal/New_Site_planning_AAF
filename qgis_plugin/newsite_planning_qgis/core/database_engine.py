"""Global lazy-greedy site planning with source rows, spatial indices and queue on disk."""
import math,re,json
from pathlib import Path
import numpy as np
import pandas as pd
from shapely.geometry import shape,Point
from session_db import Session,DiskTable,number,valid,distance,encode
from planning_policy import target_category,candidate_where,location_allowed


def prepare_references(s,r):
    s.unique('airtel',country=r.country.upper(),period=r.airtel_period)
    active_count=0
    for _,row,_ in s.input_rows('airtel',country=r.country.upper(),period=r.airtel_period):
        row['lat']=number(row['lat']);row['lon']=number(row['lon'])
        if not valid(row['lat'],row['lon']):s.issue('airtel',row,'Invalid or missing coordinates');continue
        active=bool(re.match(r'^On[ -]?Air',row['status'],re.I));modern=bool(re.search('4G|5G',row['technology'],re.I));planned=bool(re.search(r'^Planned|Under.deployment',row['status'],re.I))
        row['planning_role']='Active 4G/5G' if active and modern else 'Reserved pipeline' if planned else 'Legacy/off-air'
        row['blocking']=int((active and modern) or (planned and r.reserve_planned));active_count+=int(active and modern);s.add('airtel',row)
    if not active_count:raise ValueError('No valid on-air 4G/5G Airtel sites. Check country, status, technology and snapshot.')
    s.db.executescript('CREATE TABLE comp_stage(rid INTEGER PRIMARY KEY,gid TEXT,operator TEXT,lat REAL,lon REAL,period TEXT,tech TEXT,payload TEXT);')
    for _,row,_ in s.input_rows('competitor',country=r.country.upper()):
        row['lat']=number(row['lat']);row['lon']=number(row['lon'])
        if not valid(row['lat'],row['lon']):s.issue('competitor',row,'Invalid or missing coordinates');continue
        if not re.search('4G|5G',row['technology'],re.I):continue
        s.db.execute('INSERT INTO comp_stage(gid,operator,lat,lon,period,tech,payload) VALUES(?,?,?,?,?,?,?)',(row['id'],row['operator'],row['lat'],row['lon'],row['period'],row['technology'],encode(row)))
    if s.db.execute('SELECT operator FROM comp_stage GROUP BY operator HAVING COUNT(DISTINCT period)>1 LIMIT 1').fetchone():raise ValueError('Multiple periods for a competitor operator. Supply one snapshot per country/operator.')
    s.db.executescript("""CREATE TABLE comp_dedup AS SELECT *,ROW_NUMBER() OVER(PARTITION BY operator,gid,lat,lon,period,tech ORDER BY rid) rn FROM comp_stage;
    CREATE INDEX comp_dedup_key ON comp_dedup(operator,gid,rn);
    CREATE TABLE comp_conflict AS SELECT operator,gid FROM comp_dedup WHERE rn=1 GROUP BY operator,gid HAVING COUNT(*)>1;
    CREATE INDEX comp_conflict_key ON comp_conflict(operator,gid);""")
    for payload,rn,conflict in s.db.execute('SELECT c.payload,c.rn,EXISTS(SELECT 1 FROM comp_conflict x WHERE x.operator=c.operator AND x.gid=c.gid) FROM comp_dedup c ORDER BY c.rid'):
        s.check();row=json.loads(payload)
        if row['id'].lower() in ('','-','nan','none','null','n/a'):s.issue('competitor',row,'Missing/placeholder competitor site ID')
        elif rn>1:s.issue('competitor',row,'Repeated identical site record; first retained')
        elif conflict:s.issue('competitor',row,'Ambiguous site ID with multiple coordinates/attributes; all versions excluded')
        else:s.add('competitors',row)
    s.db.executescript('DROP TABLE comp_stage; DROP TABLE comp_dedup; DROP TABLE comp_conflict;')
    for role,table in (('infrastructure','infrastructure'),('exclusions','buffers')):
        for _,row,_ in s.input_rows(role,country=r.country.upper()):
            row['lat']=number(row['lat']);row['lon']=number(row['lon'])
            if not valid(row['lat'],row['lon']):
                if role=='exclusions' or getattr(r,'max_'+row.get('kind','').lower()+'_km',0)>0:raise ValueError(f'{role}: invalid constraint coordinates.')
                s.issue(role,row,'Invalid infrastructure reference coordinates');continue
            if role=='exclusions':
                row['radius_m']=number(row['radius_m'])
                if not math.isfinite(row['radius_m']) or row['radius_m']<=0:raise ValueError('No-build buffer radius_m must be finite and positive.')
            s.add(table,row)
    for kind in ('road','backhaul','power'):
        if getattr(r,'max_'+kind+'_km')>0 and not s.db.execute("SELECT 1 FROM infrastructure WHERE lower(json_extract(payload,'$.kind'))=? LIMIT 1",(kind,)).fetchone() and not s.db.execute("SELECT 1 FROM network WHERE json_extract(payload,'$.kind')=? LIMIT 1",(kind,)).fetchone():raise ValueError(f'{kind.title()} limit enabled but no valid {kind} infrastructure reference points were supplied.')
    s.db.commit()


def nearest_fields(s,row):
    d,a=s.nearest('airtel',row['lat'],row['lon'],"json_extract(t.payload,'$.blocking')=1")
    row.update(nearest_airtel_km=d,nearest_airtel_id=a.get('id',''))
    d,c=s.nearest('competitors',row['lat'],row['lon'])
    row.update(nearest_competitor_km=d,nearest_competitor_id=c.get('id',''),nearest_competitor_operator=c.get('operator',''),nearest_competitor_period=c.get('period',''))


def prepare_grids_and_demand(s,r,paths,notes):
    scope={'country':r.country.upper(),'size':r.grid_size,'period':r.grid_period};s.unique('grid',**scope)
    if r.mode=='Population':
        if not paths.get('population'):raise ValueError('Population is absent. Supply population data or select MR preview.')
        s.unique('population',country=r.country.upper())
        for _,row,_ in s.input_rows('population',country=r.country.upper()):
            value=number(row['value'])
            if not math.isfinite(value) or value<0:s.issue('population',row,'Missing, non-numeric or negative population')
    for index,(_,row,geometry) in enumerate(s.input_rows('grid',**scope)):
        row['lat']=number(row['lat']);row['lon']=number(row['lon'])
        if not valid(row['lat'],row['lon']):s.issue('grid',row,'Invalid or missing coordinates');continue
        row['category']=row['category'].title()
        for key in ('coverage','speed','town','site_present'):row.setdefault(key,'')
        row['area_km2']=number(row['area']) if 'area' in row else (1. if r.grid_size=='1km' else 9.)
        if not math.isfinite(row['area_km2']) or row['area_km2']<=0:raise ValueError('Actual polygon areas must be finite and positive.')
        value=float('nan')
        if r.mode=='Population':
            found=s.db.execute("SELECT payload FROM inputs WHERE role='population' AND country=? AND gid=? LIMIT 1",(r.country.upper(),row['id'])).fetchone()
            if found:value=number(json.loads(found[0])['value'])
            if value<0 or not math.isfinite(value):value=float('nan')
            row['population']=value if r.population_kind=='People per grid' else value*row['area_km2'];row['density_per_km2']=row['population']/row['area_km2'];row['demand']=row['population']
        else:row.update(population=None,density_per_km2=None,demand=number(row.get('mr','')))
        nearest_fields(s,row);poor=target_category(row['category'],r);good=math.isfinite(row['demand']) and row['demand']>0;density=r.mode!='Population' or row['density_per_km2']>=r.min_density
        row['target']=int(poor and good and density);row['severity_weight']=r.red_weight if row['category']=='Red' else r.amber_weight if row['category']=='Amber' else 0.
        row['competitor_near']=int(row['nearest_competitor_km']<=r.competitor_radius_km)
        row['weighted_demand']=(row['demand'] if good else 0)*row['severity_weight']*(1+r.competitor_boost*row['competitor_near'])
        row['candidate_exclusion']='Not an eligible demand grid' if not row['target'] else 'Grid site-present flag: assess optimization or upgrade' if r.respect_site_present and row['site_present'].lower() in ('yes','y','true','1') else 'Near active/pipeline Airtel site: assess optimization or upgrade' if row['nearest_airtel_km']<r.min_airtel_km else ''
        row['status']='Not Red/Amber' if not poor else 'Missing/invalid/zero demand' if not good else 'Below minimum population density' if not density else 'Unassigned target'
        row.update(assigned_candidate='',assignment_distance_km=None,assigned_demand=0.,assigned_fraction=0.,assigned_site_count=0)
        if geometry:row['geometry_basis']='Supplied grid polygon; preserved'
        else:
            from terrain_masks import nominal_grid_polygon
            from shapely.geometry import mapping
            geometry=encode(mapping(nominal_grid_polygon(row['lat'],row['lon'],1000 if r.grid_size=='1km' else 3000)))
            row['geometry_basis']='Reconstructed nominal cell from source centroid and grid size'
        s.add('grids',row,geometry)
        if index%2000==1999:s.db.commit();s.log(f'Prepared {index+1:,} grids on disk')
    if not s.count('grids'):raise ValueError('No valid grids match country, resolution and snapshot.')
    s.db.execute("CREATE INDEX grid_target ON grids(json_extract(payload,'$.target'))")
    s.unique('demand_points',country=r.country.upper())
    s.db.execute('CREATE TABLE fine(gid TEXT,payload TEXT)');s.db.execute('CREATE INDEX fine_grid ON fine(gid)')
    for _,row,_ in s.input_rows('demand_points',country=r.country.upper()):
        parent=s.db.execute('SELECT payload,geometry FROM grids WHERE gid=?',(row['grid_id'],)).fetchone()
        if not parent:s.issue('demand_points',row,'Parent grid outside selected scope; ignored');continue
        parent_geometry=parent[1];parent=json.loads(parent[0]);row['lat']=number(row['lat']);row['lon']=number(row['lon']);row['value']=number(row['value'])
        if not valid(row['lat'],row['lon']) or not math.isfinite(row['value']) or row['value']<0:raise ValueError('Demand points require valid coordinates and non-negative finite values.')
        if parent_geometry and not shape(json.loads(parent_geometry)).covers(Point(row['lon'],row['lat'])):
            raise ValueError(f"Demand point {row['id']} is outside its parent grid polygon {row['grid_id']}. Correct the spatial join.")
        if parent['target']:s.db.execute('INSERT INTO fine VALUES(?,?)',(row['grid_id'],encode(row)))
    detailed=0;side=1 if r.grid_size=='1km' else 3
    for rid,g,_ in s.records('grids',"json_extract(payload,'$.target')=1"):
        total,count=s.db.execute("SELECT SUM(json_extract(payload,'$.value')),COUNT(*) FROM fine WHERE gid=?",(g['id'],)).fetchone()
        if count and not math.isclose(total,g['demand'],rel_tol=1e-6,abs_tol=1e-6):raise ValueError(f"Demand point sum for grid {g['id']} ({total:g}) must match grid demand ({g['demand']:g}) within 0.0001%.")
        rows=({'point_id':'GRID:'+g['id'],'grid_id':g['id'],'lat':g['lat'],'lon':g['lon'],'demand':g['demand'],'basis':'Coarse grid demand at centroid','grid_index':rid},) if not count else (dict(point_id=d['id'],grid_id=g['id'],lat=d['lat'],lon=d['lon'],demand=d['value'],basis='Supplied detailed demand point',grid_index=rid) for payload, in s.db.execute('SELECT payload FROM fine WHERE gid=?',(g['id'],)) for d in [json.loads(payload)])
        detailed+=bool(count)
        for d in rows:
            if d['demand']<=0:continue
            comp=g['nearest_competitor_km'] if not count else s.nearest('competitors',d['lat'],d['lon'])[0]
            d['weight']=d['demand']*g['severity_weight']*(1+r.competitor_boost*(comp is not None and comp<=r.competitor_radius_km));d.update(assigned_candidate='',distance_km=None,category=g['category'])
            s.add('demand',d)
    s.db.execute('DROP TABLE fine');s.db.commit()
    notes.append(f'{detailed:,} target grids use reconciled detailed demand; others use coarse grid-centroid demand.')


def candidate_sources(s,r):
    from siting import offset_coordinates
    if r.location_mode=='Supplied candidates':
        s.unique('candidates',country=r.country.upper())
        if not s.count('inputs',"role='candidates'"):raise ValueError('Supplied candidates mode requires a candidate source; no centroid fallback.')
        for _,row,_ in s.input_rows('candidates',country=r.country.upper()):
            row['source_candidate_id']=row.pop('id');row['location_basis']='Supplied candidate coordinate';yield row
    elif r.location_mode=='Settlement search' and r.building_rule!='Rooftop search':
        sequence=0;side=1000 if r.grid_size=='1km' else 3000
        for _,g,geometry in s.records('grids',candidate_where(r),order='gid'):
            polygon=shape(json.loads(geometry));seeds=[]
            for payload, in s.db.execute("SELECT payload FROM demand WHERE json_extract(payload,'$.grid_id')=? AND json_extract(payload,'$.basis')='Supplied detailed demand point' ORDER BY json_extract(payload,'$.demand') DESC LIMIT 8",(g['id'],)):
                d=json.loads(payload);seeds.append((d['lat'],d['lon'],'Detailed demand seed'))
            if s.buildings:
                quadrants={}
                for feature in s.buildings.iter_features(polygon.bounds):
                    s.check();point=shape(feature['geometry']).representative_point()
                    if not polygon.covers(point):continue
                    key=(point.x>=g['lon'],point.y>=g['lat']);a=quadrants.setdefault(key,[0.,0.,0]);a[0]+=point.y;a[1]+=point.x;a[2]+=1
                seeds.extend((a[0]/a[2],a[1]/a[2],'Mapped settlement-cluster seed') for a in quadrants.values())
            if not seeds:seeds=[(g['lat'],g['lon'],'No fine demand/building evidence: coarse centroid fallback')]
            offset=max(50.,r.building_clearance_m+30);east=np.array([0.,offset,-offset,0.,0.]);north=np.array([0.,0.,0.,offset,-offset])
            seen=set()
            for lat,lon,basis in seeds:
                ys,xs=offset_coordinates(lat,lon,east,north)
                for y,x in zip(ys,xs):
                    identity=(round(float(y),9),round(float(x),9))
                    if identity in seen:continue
                    seen.add(identity);sequence+=1
                    yield dict(source_candidate_id=f'SETTLEMENT_{sequence:08d}',grid_id=g['id'],country=r.country.upper(),lat=float(y),lon=float(x),feasible='unknown',location_basis=basis+'; desktop alternative, physical feasibility unknown',location_source='Demand/building-guided search',survey_verified='No')
    elif r.building_rule=='Rooftop search':
        if s.buildings is None:raise ValueError('Rooftop search requires building footprints.')
        side=1 if r.grid_size=='1km' else 3
        for feature in s.buildings.iter_features():
            point=shape(feature['geometry']).representative_point();parent=None
            for payload,geom in s.db.execute('SELECT t.payload,t.geometry FROM grids_rt b JOIN grids t ON t.rid=b.rid WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(point.x,point.x,point.y,point.y)):
                proposed=json.loads(payload)
                if geom and shape(json.loads(geom)).covers(point) and location_allowed(proposed,r):
                    if r.location_policy=='Current behaviour' and not proposed['target']:continue
                    parent=proposed;break
            if parent is None:continue
            p=feature['properties'];yield dict(source_candidate_id='ROOF:'+str(p['building_id']),grid_id=parent['id'],country=r.country.upper(),lat=point.y,lon=point.x,feasible='unknown',location_basis='Footprint interior point; roof structurally unverified',location_source=p.get('source','Open footprint input'),survey_verified='No')
    else:
        side=1000 if r.grid_size=='1km' else 3000;offsets=np.array([[0.,0.]])
        if r.location_mode=='Area search':
            n=max(1,int(math.ceil(side/r.search_spacing_m)));axis=-side/2+(np.arange(n)+.5)*side/n;offsets=np.unique(np.array([(x,y) for x in axis for y in axis]+[(0.,0.)]),axis=0)
        index=0
        for _,g,_ in s.records('grids',candidate_where(r),order='gid'):
            lat,lon=offset_coordinates(g['lat'],g['lon'],offsets[:,0],offsets[:,1])
            for y,x in zip(lat,lon):
                index+=1;yield dict(source_candidate_id=f'SEARCH_{index:08d}',grid_id=g['id'],country=r.country.upper(),lat=float(y),lon=float(x),feasible='unknown',location_basis='Nominal-grid area search' if r.location_mode=='Area search' else 'Quick demo grid centroid; approximate location' if r.location_mode=='Quick demo (grid centroid)' else 'Legacy grid centroid',location_source='Generated desktop search',survey_verified='No')


class RejectionRow(dict):
    def __setitem__(self,key,value):
        if key=='rejection' and value:
            reasons=self.setdefault('rejection_reasons',[])
            if value not in reasons:reasons.append(value)
        super().__setitem__(key,value)

def prepare_candidates(s,r,notes):
    side=1000 if r.grid_size=='1km' else 3000
    if r.location_mode in ('Area search','Grid centroid (legacy)') and r.building_rule!='Rooftop search':
        grids=s.count('grids',candidate_where(r));n=max(1,int(math.ceil(side/r.search_spacing_m))) if r.location_mode=='Area search' else 1;expected=grids*(n*n+int(n%2==0))
        s.log(f'Preflight: {grids:,} location grids; {expected:,} generated alternatives; configured limit {r.max_candidate_points:,}.')
        if expected>r.max_candidate_points:raise ValueError(f'Candidate alternatives exceed configured limit: expected {expected:,}; configured {r.max_candidate_points:,}. Increase the limit or search spacing before rerunning.')
    max_buffer=s.db.execute("SELECT MAX(json_extract(payload,'$.radius_m')) FROM buffers").fetchone()[0] or 0
    def flush(batch):
        if s.buildings is not None:
            from dataclasses import replace
            frame=pd.DataFrame(batch);effective=replace(r,building_clearance_m=max(r.building_clearance_m,r.site_compound_radius_m));s.buildings.annotate(frame,effective);batch=frame.to_dict('records')
        for row in batch:
            # Always retain the water rejection, including when building screening also rejects.
            water_reason=row.pop('_water_rejection','')
            reasons=row.setdefault('rejection_reasons',[])
            if row.get('rejection') and row['rejection'] not in reasons:reasons.append(row['rejection'])
            if water_reason:
                if water_reason not in reasons:reasons.insert(0,water_reason)
            row['rejection']=water_reason or (reasons[0] if reasons else '')
            rid=s.add('candidates',row);s.db.execute('UPDATE candidates SET allowed=? WHERE rid=?',(int(not row['rejection']),rid))
        s.db.commit()
    batch=[];processed=0
    for row in candidate_sources(s,r):
        s.check();processed+=1
        if r.location_mode!='Quick demo (grid centroid)' and processed>r.max_candidate_points:raise ValueError('Candidate alternatives exceed configured limit; increase the limit or use a wider search spacing.')
        row=RejectionRow(row)
        row['lat']=number(row['lat']);row['lon']=number(row['lon'])
        if not valid(row['lat'],row['lon']):s.issue('candidates',row,'Invalid candidate coordinates; excluded');continue
        accuracy_supplied=str(row.get('accuracy_m','')).strip()
        row['rejection']='';row['rejection_reasons']=[];row['suitability']=number(row.get('suitability') or '1');row['survey_verified']=row.get('survey_verified','Unknown');row['location_source']=row.get('location_source','Not supplied');row['accuracy_m']=number(row.get('accuracy_m',''))
        if r.location_mode=='Supplied candidates' and str(row.get('feasible','')).lower() not in ('yes','true','1'):row['rejection']='Candidate feasibility not explicitly confirmed'
        if not 0<=row['suitability']<=1:row['rejection']='Suitability must be between 0 and 1'
        accuracy=row.get('accuracy_m')
        if accuracy_supplied and (not math.isfinite(accuracy) or accuracy<0):row['rejection']='Invalid supplied horizontal survey accuracy'
        # Missing survey accuracy remains unknown; malformed supplied nonempty values fail screening.
        parent=s.db.execute('SELECT rid,payload,geometry FROM grids WHERE gid=?',(row['grid_id'],)).fetchone()
        if not parent:
            row['rejection']='Parent grid outside selected country/resolution/snapshot';row.update(parent_grid_index=None,parent_town='',parent_category='',offset_from_grid_centroid_m=None)
        else:
            pid,g,geometry=parent;g=json.loads(g);row.update(parent_grid_index=pid,parent_town=g['town'],parent_category=g['category'],parent_lat=g['lat'],parent_lon=g['lon'],parent_site_present=g['site_present'])
            if not location_allowed(g,r):row['rejection']='Parent grid is disallowed by location policy'
            row['offset_from_grid_centroid_m']=distance(row['lat'],row['lon'],g['lat'],g['lon'])*1000
            if geometry:
                if not shape(json.loads(geometry)).covers(Point(row['lon'],row['lat'])):row['rejection']='Outside actual parent grid polygon'
                row['polygon_constraint_check']=('Actual parent grid boundary checked' if g.get('geometry_basis','').startswith('Supplied') else 'Nominal reconstructed parent grid footprint checked')
            elif row['offset_from_grid_centroid_m']>side/math.sqrt(2)+10:row['rejection']='Outside nominal parent-grid envelope; verify grid assignment'
        from terrain_masks import screen_water
        water_reason=screen_water(s,row);row['water_screening']='Rejected: '+water_reason if water_reason else 'Passed supplied land and water polygons'
        row['_water_rejection']=water_reason
        nearest_fields(s,row)
        if row['nearest_airtel_km']<r.min_airtel_km:row['rejection']='Too near active/reserved Airtel site'
        if parent and r.respect_site_present and g['site_present'].lower() in ('yes','true','y','1'):row['rejection']='Parent grid site-present flag'
        for geometry, in s.db.execute('SELECT t.geometry FROM no_build_rt b JOIN no_build t ON b.rid=t.rid WHERE b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?',(row['lon'],row['lon'],row['lat'],row['lat'])):
            if shape(json.loads(geometry)).covers(Point(row['lon'],row['lat'])):row['rejection']='Inside/on no-build polygon';break
        if max_buffer:
            for _,ex,dist,_ in s.spatial('buffers',row['lat'],row['lon'],max_buffer/1000):
                if dist<=ex['radius_m']/1000:row['rejection']='No-build buffer: '+ex['id'];break
        for kind in ('road','backhaul','power'):
            d,_=s.nearest('infrastructure',row['lat'],row['lon'],"lower(json_extract(t.payload,'$.kind'))=?",(kind,));row['nearest_'+kind+'_km']=d
            if getattr(r,'max_'+kind+'_km')>0 and d>getattr(r,'max_'+kind+'_km'):row['rejection']=f'Beyond {kind} reference-point distance limit'
        if getattr(s,'engineering',None):
            for message in s.engineering.screen(row):row['rejection']=message
        for kind in ('road','backhaul','power'):
            value=row.get('nearest_'+kind+'_km');limit=getattr(r,'max_'+kind+'_km')
            if limit and value is not None and math.isfinite(value) and value<=limit:
                row['rejection_reasons']=[x for x in row['rejection_reasons'] if x!=f'Beyond {kind} reference-point distance limit']
        row['rejection']=row['rejection_reasons'][0] if row['rejection_reasons'] else ''
        row.update(mapped_buildings_within_radius=None,mapped_footprint_area_m2=None,nearest_mapped_building_m=None,containing_building_id='',containing_building_area_m2=None,building_factor=1.,building_context='No building dataset supplied',selected_candidate='',initial_demand=0.,initial_weighted_benefit=0.)
        batch.append(row)
        if len(batch)>=min(s.batch_size,256):flush(batch);batch=[]
        if processed%2000==0:s.log(f'{processed:,} candidate alternatives screened on disk')
    if batch:flush(batch)
    notes.append('Candidates, demand, assignments and the lazy-greedy priority queue are stored in a temporary SQLite database. No global candidate-demand link list is held in memory.')


def score_candidate(s,row,r,unassigned=True,record=False):
    before_decay=competitor_gain=0.
    total=benefit=red_benefit=red=amber=0.;quality=row['suitability']*row['building_factor'];count=0
    for _,d,dist,_ in s.spatial('demand',row['lat'],row['lon'],r.service_radius_km,"t.assigned=''" if unassigned else '1'):
        evidence=getattr(s,'engineering',None)
        if evidence and not evidence.rf_allows(row,d):continue
        value=d['weight']*math.exp(-r.distance_decay*(dist/r.service_radius_km)**2)*quality
        base=d['demand']*(r.red_weight if d.get('category')=='Red' else r.amber_weight);decay=math.exp(-r.distance_decay*(dist/r.service_radius_km)**2)
        before_decay+=d['weight']*quality;competitor_gain+=(d['weight']-base)*decay*quality
        total+=d['demand'];benefit+=value;count+=1
        if d.get('category')=='Red':red+=d['demand'];red_benefit+=value
        else:amber+=d['demand']
    if record:row['_score_breakdown']={'red_benefit':red_benefit,'amber_benefit':benefit-red_benefit,'competitor_uplift_after_decay':competitor_gain,'benefit_before_distance_decay':before_decay,'distance_decay_reduction':before_decay-benefit,'suitability_factor':row['suitability'],'building_factor':row['building_factor'],'final_weighted_benefit':benefit}
    return total,benefit,count,red_benefit,red,amber


def select_sites(s,r):
    from planner import SITE_COLUMNS
    eligible=s.count('candidates','allowed=1');s.log(f'{s.count("demand"):,} demand points; {s.count("candidates"):,} alternatives; {eligible:,} pass screening.')
    for index,(rid,row,_) in enumerate(s.records('candidates','allowed=1')):
        total,benefit,_,red_score,_,_=score_candidate(s,row,r);row.update(initial_demand=total,initial_weighted_benefit=benefit);s.update('candidates',rid,row);s.db.execute('UPDATE candidates SET score=?,red_score=? WHERE rid=?',(benefit,red_score,rid))
        if index%2000==1999:s.db.commit();s.log(f'Initial scores: {index+1:,}/{eligible:,}')
    rank=0;s.db.execute('CREATE TEMP TABLE local_assigned(gr TEXT,dem REAL,dist REAL)')
    while rank<r.max_sites:
        s.check();order='red_score DESC,score DESC,rid' if r.priority_policy=='Red demand first' else 'score DESC,rid'
        entry=s.db.execute('SELECT rid,payload,score,stamp FROM candidates WHERE allowed=1 ORDER BY '+order+' LIMIT 1').fetchone()
        if not entry:break
        rid,payload,old_score,stamp=entry;row=json.loads(payload)
        if old_score<=0:break
        total,value,point_count,red_score,red,amber=score_candidate(s,row,r,record=True)
        if point_count==0 or total<r.min_incremental or value<=0:s.db.execute('UPDATE candidates SET allowed=0 WHERE rid=?',(rid,));continue
        if stamp!=rank:s.db.execute('UPDATE candidates SET score=?,red_score=?,stamp=? WHERE rid=?',(value,red_score,rank,rid));continue
        alternatives=[]
        for other_id,payload in s.db.execute("SELECT rid,payload FROM candidates WHERE allowed=1 AND gid<>? AND json_extract(payload,'$.grid_id')=? ORDER BY score DESC LIMIT 50",(row['source_candidate_id'],row['grid_id'])):
            other=json.loads(payload);opportunity,score,_,rb,rd,ad=score_candidate(s,other,r)
            alternatives.append({'source_candidate_id':other['source_candidate_id'],'grid_id':other['grid_id'],'lat':other['lat'],'lon':other['lon'],'weighted_benefit':score,'incremental_demand':opportunity,'red_demand':rd,'amber_demand':ad,'red_weighted_benefit':rb})
        alternatives.sort(key=lambda a:((a['red_weighted_benefit'] if r.priority_policy=='Red demand first' else 0),a['weighted_benefit']),reverse=True)
        rank+=1;cid=f'{r.country}_{r.grid_size}_NEW_{rank:04d}'
        for alternative in alternatives[:3]:s.db.execute('INSERT INTO alternatives VALUES(?)',(encode({'selected_candidate':cid,'comparison_basis':'At selection time; up to 50 screened alternatives from same parent grid',**alternative}),))
        # Updates stream one point at a time; no huge neighborhood list.
        s.db.execute('DELETE FROM local_assigned');coarse=False
        for did,d,dist,_ in s.spatial('demand',row['lat'],row['lon'],r.service_radius_km,"t.assigned=''"):
            if getattr(s,'engineering',None) and not s.engineering.rf_allows(row,d):continue
            coarse|=d['basis']=='Coarse grid demand at centroid';d.update(assigned_candidate=cid,distance_km=dist);s.update('demand',did,d)
            s.db.execute('UPDATE demand SET assigned=?,distance=? WHERE rid=?',(cid,dist,did));s.db.execute('INSERT INTO local_assigned VALUES(?,?,?)',(d['grid_id'],d['demand'],dist))
        grid_count=s.db.execute('SELECT COUNT(DISTINCT gr) FROM local_assigned').fetchone()[0]
        for gid,dem,weighted in s.db.execute('SELECT gr,SUM(dem),SUM(dem*dist) FROM local_assigned GROUP BY gr'):
            s.db.execute('INSERT INTO selected_stats VALUES(?,?,?,?,?) ON CONFLICT(gid) DO UPDATE SET total=total+excluded.total,weighted_distance=weighted_distance+excluded.weighted_distance,count_sites=count_sites+1,ids=ids||\';\'||excluded.ids',(gid,dem,weighted,1,cid))

        result={key:row.get(key) for key in SITE_COLUMNS};result.update(rank=rank,candidate_id=cid,country=r.country,category=row['parent_category'],town=row['parent_town'],incremental_demand=total,weighted_benefit=value,assigned_grids=grid_count,assigned_points=point_count,
            score_breakdown=row['_score_breakdown'],review_status='Automatically proposed',red_demand=red,amber_demand=amber,red_weighted_benefit=red_score,policy=r.priority_policy+'; '+r.location_policy,selection_explanation=f'Rank {rank}: {red:g} Red and {amber:g} Amber incremental demand; weighted benefit {value:g}. Objective: {r.priority_policy}. Suitability {row["suitability"]:g}; building factor {row["building_factor"]:g}; distance decay {r.distance_decay:g}. Alternative comparison is local and bounded; RF remains unverified unless imported evidence applies.',location_status='Input feasible candidate; RF and acquisition review required' if r.location_mode=='Supplied candidates' else 'Quick demo only; approximate grid centroid, detailed planning required' if r.location_mode=='Quick demo (grid centroid)' else 'Desktop search point; physical feasibility unknown',demand_basis='Mixed/coarse grid demand' if coarse else 'Supplied detailed demand points',demand_unit='people (radius opportunity, not predicted coverage)' if r.mode=='Population' else 'MR records (NOT population)',reason=f"{row['parent_category']} area; {point_count} incremental demand points")
        s.add('sites',result);row['selected_candidate']=cid;s.update('candidates',rid,row)
        for other,_,dist,_ in s.spatial('candidates',row['lat'],row['lon'],r.min_new_km,'t.allowed=1'):
            if other==rid or dist<r.min_new_km or math.isclose(dist,r.min_new_km,abs_tol=1e-12):s.db.execute('UPDATE candidates SET allowed=0 WHERE rid=?',(other,))
        s.db.execute('UPDATE candidates SET allowed=0 WHERE rid=?',(rid,))
        if rank%100==0:s.db.commit();s.log(f'Selected {rank:,} locations')
    for rid,g,_ in s.records('grids'):
        stats=s.db.execute('SELECT total,weighted_distance,count_sites,ids FROM selected_stats WHERE gid=?',(g['id'],)).fetchone()
        if stats:
            total,weighted,count,ids=stats;fraction=total/g['demand'];g.update(assigned_demand=total,assigned_fraction=fraction,assigned_site_count=count,assigned_candidate=ids,assignment_distance_km=weighted/total,status='Fully assigned opportunity (unverified RF)' if fraction>=.995 else 'Partially assigned opportunity (unverified RF)');s.update('grids',rid,g)
    s.db.commit();return eligible


def run_database(paths,rules,mappings=None,log=print,cancelled=lambda:False,source_options=None):
    rules.validate()
    if not all(paths.get(role) for role in ('grid','airtel','competitor')):raise ValueError('Load grid, Airtel and competitor source files.')
    missing=[role for role in ('land_polygons','water_polygons') if not paths.get(role)]
    if missing:raise ValueError('Water protection requires Land polygons and Water polygons covering the planning area. Select coastal land boundaries plus rivers/lakes/lagoons in Data sources. Missing: '+', '.join(missing))
    s=Session(log,cancelled,rules.import_batch_size)
    try:
        s.ingest(paths,rules,mappings,source_options)
        if not s.count('land') or not s.count('water'):raise ValueError('Land and water polygon layers must be nonempty. Water screening cannot be skipped.')
        from engineering import Engineering
        s.engineering=Engineering(s,rules,paths.get('terrain'))
        prepare_references(s,rules);notes=[]
        if rules.location_mode=='Quick demo (grid centroid)':
            message='QUICK DEMO: location search OFF; one centroid per target grid. Approximate positions only; land/water and loaded feasibility checks remain active. Search alternative limit does not apply.'
            notes.append(message);log(message)
        prepare_grids_and_demand(s,rules,paths,notes)
        if s.buildings is not None:
            w,south,e,n=s.buildings.bounds;bounds=s.db.execute('SELECT min(minx),min(miny),max(maxx),max(maxy) FROM grids_rt').fetchone()
            if e<bounds[0] or w>bounds[2] or n<bounds[1] or south>bounds[3]:raise ValueError('Building extent does not overlap the selected grid scope.')
            for issue in s.buildings.issues:s.issue('buildings',issue,issue.get('reason','Invalid footprint'))
        if rules.building_rule=='Rooftop search' and s.buildings is None:raise ValueError('Rooftop search requires building footprints.')
        prepare_candidates(s,rules,notes);eligible=select_sites(s,rules)
        def scalar(expr,table='grids',where='1'):return s.db.execute(f'SELECT {expr} FROM {table} WHERE {where}').fetchone()[0]
        periods=[row[0] for row in s.db.execute("SELECT DISTINCT json_extract(payload,'$.period') FROM competitors ORDER BY 1")]
        if rules.mode=='MR preview':notes.append('MR counts are a proxy, not population, unique users, traffic or revenue.')
        if s.buildings:notes.append(f'{s.buildings.count:,} footprints stored in a temporary building index; no building counts converted into population.')
        notes.extend(['All selected coordinates passed supplied land and water polygon checks. Water/coastline source resolution and completeness still govern screening accuracy.','Grid polygons are preserved when supplied; CSV centres generate nominal 1 km/3 km footprints consistently across viewers and exports.','Global lazy-greedy marginal demand; distance assumptions do not predict RF coverage.','A run may stop before its site target when incremental demand or feasible spaced locations are exhausted.','All large source tables and working queues are on disk. Only bounded import, geometry and display batches are materialized.'])
        summary={'mode':rules.mode,'country':rules.country,'grid_size':rules.grid_size,'grid_period':rules.grid_period,'airtel_period':rules.airtel_period,'competitor_periods':periods,'grid_count':s.count('grids'),'target_grids':scalar("SUM(json_extract(payload,'$.target'))") or 0,'eligible_locations':eligible,'location_mode':rules.location_mode,'proposed_sites':s.count('sites'),'assigned_grids':s.count('selected_stats'),
            'unassigned_target_grids':scalar('COUNT(*)',where="json_extract(payload,'$.target')=1 AND json_extract(payload,'$.assigned_fraction')<.995"),'incremental_demand_total':scalar('COALESCE(SUM(total),0)','selected_stats'),'target_demand_total':scalar("COALESCE(SUM(json_extract(payload,'$.demand')),0)",where="json_extract(payload,'$.target')=1"),'demand_unit':'people (radius opportunity estimate)' if rules.mode=='Population' else 'MR records (proxy, NOT population or unique users)',
            'population_missing_poor_grids':scalar('COUNT(*)',where="json_extract(payload,'$.category') IN ('Red','Amber') AND json_extract(payload,'$.population') IS NULL") if rules.mode=='Population' else None,'invalid_coordinate_rows':scalar('COUNT(*)','issues',"json_extract(payload,'$.reason') LIKE '%coordinates%'"),'notes':notes,'method':'Global disk-backed lazy-greedy selection; exact spherical radius queries','water_screening':'Required land + water polygon screening applied','storage':'temporary SQLite','database_path':str(s.path),'map_preview_limits':{'sites':500,'grids':5000,'inventory_each':5000}}
        from database_result import DatabaseResult
        result=DatabaseResult(s,rules,summary);log(f'Complete: {summary["proposed_sites"]:,} sites planned; working database stays temporary until results are closed.');return result
    except BaseException:s.close();raise
