"""Local desktop site planner. Spherical distances, auditable greedy selection."""
from __future__ import annotations
import argparse
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import heapq
import json
import math
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

EARTH_KM = 6371.0088
ALIASES = {
 'grid': {'area':'area_km2_actual','country':'opco','id':'grid_id','lat':'centroid_lat','lon':'centroid_long',
          'category':'combined_category_wf','size':'grid_size','period':'month',
          'coverage':'coverage_category','speed':'throughput_category','mr':'total_mr_count',
          'town':'town_name','site_present':'site_present'},
 'airtel': {'country':'opco_id','id':'physical_site_id','lat':'latitude','lon':'longitude',
            'status':'physical_site_status','technology':'technology','period':'month'},
 'competitor': {'country':'OPCO_ID','id':'SiteID','lat':'Latitude','lon':'Longitude',
                'operator':'OPCO_Comp','technology':'Technology','period':'DB_Period'},
 'population': {'country':'opco','id':'grid_id','value':'population'},
 'candidates': {'country':'opco','id':'candidate_id','grid_id':'grid_id','lat':'latitude','lon':'longitude',
                'feasible':'feasible','suitability':'suitability','survey_verified':'survey_verified',
                'accuracy_m':'accuracy_m','location_source':'location_source'},
 'demand_points': {'country':'opco','id':'point_id','grid_id':'grid_id','lat':'latitude','lon':'longitude','value':'demand'},
 'exclusions': {'country':'opco','id':'exclusion_id','lat':'latitude','lon':'longitude','radius_m':'radius_m'},
 'infrastructure': {'country':'opco','id':'reference_id','kind':'kind','lat':'latitude','lon':'longitude'}
}
REQUIRED = {'grid':['country','id','lat','lon','category','size','period'],
 'airtel':['country','id','lat','lon','status','technology','period'],
 'competitor':['country','id','lat','lon','operator','technology','period'],
 'population':['country','id','value'],
 'candidates':['country','id','grid_id','lat','lon','feasible'],
 'demand_points':['country','id','grid_id','lat','lon','value'],
 'exclusions':['country','id','lat','lon','radius_m'],
 'infrastructure':['country','id','kind','lat','lon']}

@dataclass
class Rules:
    country: str = 'NG'
    grid_size: str = '1km'
    grid_period: str = "Sep'26"
    airtel_period: str = 'Sep_2026'
    mode: str = 'Population'
    population_kind: str = 'People per grid'
    service_radius_km: float = 1.5
    min_airtel_km: float = 0.5
    min_new_km: float = 1.0
    competitor_radius_km: float = 1.5
    competitor_boost: float = 0.25
    red_weight: float = 1.0
    amber_weight: float = 0.6
    min_density: float = 0.0
    min_incremental: float = 1.0
    max_sites: int = 30
    import_batch_size: int = 2000
    building_import_batch_size: int = 400000
    reserve_planned: bool = True
    respect_site_present: bool = True

    location_mode: str = 'Area search'
    search_spacing_m: float = 250.0
    distance_decay: float = 0.8
    max_candidate_points: int = 500000
    max_road_km: float = 0.0
    max_backhaul_km: float = 0.0
    max_power_km: float = 0.0

    building_storage: str = 'Disk index'
    building_rule: str = 'Ground clearance'
    building_clearance_m: float = 10.0
    building_context_radius_m: float = 500.0
    building_density_boost: float = 0.15
    building_min_confidence: float = 0.75
    min_roof_area_m2: float = 80.0
    building_clip_bbox: str = ''

    def validate(self):
        if self.building_storage not in ('Disk index','Regional memory'):raise ValueError('Choose Disk index or Regional memory for building storage.')
        if self.building_rule=='Rooftop search' and self.location_mode in ('Grid centroid (legacy)','Quick demo (grid centroid)'):raise ValueError('Rooftop search requires Area search or Supplied candidates location mode.')
        if self.building_rule not in ('Ground clearance','Context only','Rooftop search'):
            raise ValueError('Choose a valid building rule.')
        for name in ('building_clearance_m','building_context_radius_m','building_density_boost','min_roof_area_m2'):
            value=getattr(self,name)
            if not math.isfinite(value) or value<0:raise ValueError(f'{name} must be finite and nonnegative.')
        if not 1<=self.building_context_radius_m<=5000:raise ValueError('Building context radius must be 1–5000 m.')
        if not math.isfinite(self.building_min_confidence) or not 0<=self.building_min_confidence<=1:raise ValueError('Building confidence must be 0–1.')
        from siting import MODES
        if self.location_mode not in MODES:raise ValueError('Choose Area search, Supplied candidates, Quick demo (grid centroid) or Grid centroid (legacy).')
        if not math.isfinite(self.search_spacing_m) or not 100<=self.search_spacing_m<=3000:
            raise ValueError('Search spacing must be between 100 and 3000 metres. It is not an accuracy claim.')
        if not isinstance(self.max_candidate_points,int) or self.max_candidate_points<1:raise ValueError('Maximum candidate alternatives must be a positive integer.')
        if not isinstance(self.import_batch_size,int) or not 100<=self.import_batch_size<=20000:raise ValueError('Import batch size must be 100–20,000 rows.')
        if isinstance(self.building_import_batch_size,bool) or not isinstance(self.building_import_batch_size,int) or not 100<=self.building_import_batch_size<=400000:raise ValueError('Building import batch size must be 100–400,000 rows.')
        if not math.isfinite(self.distance_decay) or not 0<=self.distance_decay<=10:raise ValueError('Distance-decay strength must be 0–10.')
        if self.mode not in ('Population','MR preview'):
            raise ValueError('Choose Population or MR preview.')
        if self.population_kind not in ('People per grid','People per km2'):
            raise ValueError('Unknown population unit.')
        if self.grid_size not in ('1km','3km'):
            raise ValueError('Choose a single grid resolution: 1km or 3km.')
        if not self.country.strip() or not self.grid_period or not self.airtel_period:
            raise ValueError('Country and both snapshot periods must be selected.')
        numeric = ['service_radius_km','min_airtel_km','min_new_km','competitor_radius_km',
                   'competitor_boost','red_weight','amber_weight','min_density','min_incremental','max_road_km','max_backhaul_km','max_power_km']
        for key in numeric:
            v = getattr(self,key)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{key} must be a finite, non-negative number.')
        if self.service_radius_km <= 0 or self.service_radius_km > 100:
            raise ValueError('Service radius must be >0 and <=100 km.')
        if not isinstance(self.max_sites,int) or isinstance(self.max_sites,bool) or self.max_sites<1:
            raise ValueError('Maximum sites must be a positive integer; there is no 10,000-site cap.')
        if self.red_weight <= 0 or self.amber_weight <= 0:
            raise ValueError('Red and Amber weights must be positive.')
        if self.mode == 'MR preview' and self.min_density:
            raise ValueError('Population-density filter requires Population mode.')

def read_csv(path, nrows=None):
    """Preserve IDs; fail on malformed rows, fall back to the competitor file encoding."""
    for encoding in ('utf-8-sig','cp1252'):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding, nrows=nrows,
                               keep_default_na=False).rename(columns=lambda x:x.strip())
        except UnicodeDecodeError:
            continue
    raise ValueError(f'Cannot decode {Path(path).name}. Save it as UTF-8 CSV.')

def canonical(frame, kind, mapping=None):
    mapping = mapping or ALIASES[kind]
    missing=[f'{k} → {mapping.get(k,"(not mapped)")}' for k in REQUIRED[kind]
             if not mapping.get(k) or mapping[k] not in frame]
    if missing:
        raise ValueError(f'{kind}: missing columns: {", ".join(missing)}. Check column mapping.')
    out=pd.DataFrame(index=frame.index)
    for key,col in mapping.items():
        if col and col in frame:
            out[key]=frame[col].astype(str).str.strip()
    out['source_row']=np.arange(len(out))+2
    if 'country' in out: out['country']=out.country.str.upper()
    return out

def numeric(series):
    return pd.to_numeric(series.astype(str).str.replace(',','',regex=False).str.strip(),errors='coerce')

def xyz(frame):
    lat=np.deg2rad(frame.lat.to_numpy(float)); lon=np.deg2rad(frame.lon.to_numpy(float))
    return np.column_stack([np.cos(lat)*np.cos(lon),np.cos(lat)*np.sin(lon),np.sin(lat)])

def chord(km): return 2*np.sin(min(float(km)/EARTH_KM,np.pi)/2)

def km_from_chord(value): return 2*EARTH_KM*np.arcsin(np.clip(np.asarray(value)/2,0,1))

def nearest(query, reference):
    if len(reference)==0:
        return np.full(len(query),np.nan),np.full(len(query),-1,dtype=int)
    d,i=cKDTree(xyz(reference)).query(xyz(query),k=1)
    return km_from_chord(d),i

def valid_coords(frame):
    frame=frame.copy(); frame['lat']=numeric(frame.lat);frame['lon']=numeric(frame.lon)
    ok=frame.lat.between(-90,90)&frame.lon.between(-180,180)&np.isfinite(frame.lat)&np.isfinite(frame.lon)
    return frame.loc[ok].copy(),frame.loc[~ok].copy()

def validate_ids(d, label, keys):
    if (d[keys].eq('').any(axis=1)).any():
        raise ValueError(f'{label}: blank identifiers. Correct them before planning.')
    if d.duplicated(keys).any():
        examples=d.loc[d.duplicated(keys,keep=False),keys].head(4).to_dict('records')
        raise ValueError(f'{label}: duplicate keys {examples}. Resolve duplicate records first.')

SITE_COLUMNS=['rank','candidate_id','country','grid_id','lat','lon','category','town',
 'incremental_demand','weighted_benefit','assigned_grids','nearest_airtel_km',
 'nearest_airtel_id','nearest_competitor_km','nearest_competitor_id','nearest_competitor_operator',
 'nearest_competitor_period','demand_unit','reason','assigned_points','source_candidate_id','location_basis',
 'location_source','location_status','survey_verified','accuracy_m','offset_from_grid_centroid_m',
 'demand_basis','suitability','nearest_road_km','nearest_backhaul_km','nearest_power_km','mapped_buildings_within_radius','mapped_footprint_area_m2','nearest_mapped_building_m','containing_building_id','containing_building_area_m2','building_factor','building_context','water_screening']

@dataclass
class Plan:
    sites: pd.DataFrame
    grids: pd.DataFrame
    airtel: pd.DataFrame
    competitors: pd.DataFrame
    issues: pd.DataFrame
    summary: dict
    rules: Rules
    candidate_assessment: pd.DataFrame
    demand_points: pd.DataFrame
    buildings_context: object = None
    polygon_context: object = None
    input_metadata: dict = None


def plan_frames(raw, rules, mappings=None, log=print, cancelled=lambda:False):
    """Small-fixture reference engine; desktop/CLI use database-backed run_paths."""
    rules.validate(); mappings=mappings or ALIASES
    source={k:canonical(v,k,mappings.get(k)) for k,v in raw.items() if v is not None and not k.startswith('_')}
    if not all(k in source for k in ('grid','airtel','competitor')):
        raise ValueError('Load the grid, Airtel site master and competitor CSVs.')
    issues=[]; notes=[]
    def reject(d,kind,reason):
        for row in d.to_dict('records'):
            issues.append({'source':kind,'source_row':row.get('source_row'),
                           'id':row.get('id',''),'reason':reason})
    g=source['grid']; g=g[(g.country==rules.country.upper()) &
       (g['size'].str.lower().str.replace(' ','')==rules.grid_size)&(g.period==rules.grid_period)].copy()
    if g.empty: raise ValueError('No grids match country, resolution and grid period.')
    validate_ids(g,'Grids',['country','id'])
    g,bad=valid_coords(g);reject(bad,'grid','Invalid or missing coordinates')
    if g.empty: raise ValueError('No valid grid coordinates.')
    g=g.sort_values('id',kind='stable').reset_index(drop=True)
    g['_index']=np.arange(len(g))
    g['category']=g.category.str.title()
    for c in ('coverage','speed','town','site_present'):
        if c not in g: g[c]=''
    g['area_km2']=1.0 if rules.grid_size=='1km' else 9.0
    if 'area' in g:
        g['area_km2']=numeric(g.area)
        if not(np.isfinite(g.area_km2)&g.area_km2.gt(0)).all():raise ValueError('Actual polygon areas must be finite and positive.')
    g['population']=np.nan;g['density_per_km2']=np.nan
    if rules.mode=='Population':
        if 'population' not in source:
            raise ValueError('Population is absent from all supplied files. Load a population CSV or select MR preview.')
        p=source['population'];p=p[p.country==rules.country.upper()].copy()
        validate_ids(p,'Population',['country','id'])
        p['value']=numeric(p.value)
        invalid=(~np.isfinite(p.value))|(p.value<0)
        reject(p[invalid],'population','Missing, non-numeric or negative population')
        p=p.loc[~invalid]
        extra=p.loc[~p.id.isin(g.id)]
        notes.append(f'{len(extra)} population keys outside selected grid scope (not used).')
        values=g.id.map(p.set_index('id').value)
        g['population']=values if rules.population_kind=='People per grid' else values*g.area_km2
        g['density_per_km2']=g.population/g.area_km2
        g['demand']=g.population
        missing_poor=int((g.category.isin(['Red','Amber']) & g.population.isna()).sum())
        notes.append(f'{missing_poor:,} Red/Amber grids have unknown population and are excluded; zero population is also excluded.')
        demand_unit='people (centroid-radius opportunity estimate)'
    else:
        if 'mr' not in g: raise ValueError('MR preview requires the MR count column.')
        g['demand']=numeric(g.mr)
        demand_unit='MR records (proxy, NOT population or unique users)'
        notes.append('MR PREVIEW: MR counts are not population, users, traffic or revenue.')
    a=source['airtel']; a=a[(a.country==rules.country.upper())&(a.period==rules.airtel_period)].copy()
    if a.empty: raise ValueError('No Airtel sites match the selected country and Airtel period.')
    a,bad=valid_coords(a);reject(bad,'airtel','Invalid or missing coordinates')
    # Exclude off-air sites; reserve planned/under-deployment sites regardless of technology.
    active=a.status.str.match(r'^On[ -]?Air',case=False,na=False)
    modern=a.technology.str.contains(r'4G|5G',case=False,regex=True)
    planned=a.status.str.contains(r'^Planned|Under.deployment',case=False,regex=True)
    a['planning_role']=np.where(active&modern,'Active 4G/5G',
                              np.where(planned,'Reserved pipeline','Legacy/off-air'))
    blockers=a.loc[(active&modern)|(planned&rules.reserve_planned)].copy()
    if not (active&modern).any():
        raise ValueError('No valid on-air 4G/5G Airtel sites. Check site status, technology and snapshot.')
    validate_ids(a,'Airtel site snapshot',['country','id'])
    c=source['competitor']; c=c[c.country==rules.country.upper()].copy()
    c,bad=valid_coords(c);reject(bad,'competitor','Invalid or missing coordinates')
    c=c[c.technology.str.contains('4G|5G',case=False,regex=True)].copy()
    # A competitor source is one snapshot per country/operator, not a time series.
    if not c.empty and (c.groupby('operator').period.nunique()>1).any():
        raise ValueError('Multiple periods for a competitor operator. Supply one snapshot per country/operator.')
    # Competitor inventory contains repeated IDs and conflicting locations. Quarantine
    # ambiguous identities rather than choosing an arbitrary tower coordinate.
    unknown=c.id.str.lower().isin(['','-','nan','none','null','n/a'])
    reject(c.loc[unknown],'competitor','Missing/placeholder competitor site ID')
    c=c.loc[~unknown].copy()
    keys=['country','operator','id']
    identical=c.duplicated(keys+['lat','lon','period','technology'],keep='first')
    reject(c.loc[identical],'competitor','Repeated identical site record; first retained')
    c=c.loc[~identical].copy()
    conflict=c.duplicated(keys,keep=False)
    reject(c.loc[conflict],'competitor','Ambiguous site ID with multiple coordinates/attributes; all versions excluded')
    rejected_competitors=int(unknown.sum()+identical.sum()+conflict.sum())
    c=c.loc[~conflict].copy()
    if rejected_competitors:
        notes.append(f'{rejected_competitors} competitor records excluded for missing IDs, duplicates or ambiguous locations; inspect data-quality issues.')
    if c.empty: notes.append('No valid 4G/5G competitor records; competitor proximity unavailable.')
    else: notes.append('Competitor source periods: '+', '.join(sorted(c.period.unique()))+
                       '. Historical site locations are not evidence of current coverage.')
    log(f'{len(g):,} grids; {len(a):,} valid Airtel sites; {len(c):,} 4G/5G competitor sites.')
    g['nearest_airtel_km'],ai=nearest(g,blockers)
    g['nearest_airtel_id']=[blockers.iloc[i]['id'] if i>=0 else '' for i in ai]
    g['nearest_competitor_km'],ci=nearest(g,c)
    for col in ['id','operator','period']:
        g['nearest_competitor_'+col]=[c.iloc[i][col] if i>=0 else '' for i in ci]
    g['competitor_near']=g.nearest_competitor_km.le(rules.competitor_radius_km)
    g['status']='Not Red/Amber'
    poor=g.category.isin(['Red','Amber'])
    good_demand=np.isfinite(g.demand)&g.demand.gt(0)
    density_ok=(g.density_per_km2.ge(rules.min_density) if rules.mode=='Population'
                else pd.Series(True,index=g.index))
    g.loc[poor&~good_demand,'status']='Missing/invalid/zero demand'
    g.loc[poor&good_demand&~density_ok,'status']='Below minimum population density'
    target=poor&good_demand&density_ok
    g.loc[target,'status']='Unassigned target'
    # Existing-site presence changes candidate eligibility, not demand eligibility.
    near=g.nearest_airtel_km.lt(rules.min_airtel_km)
    present=g.site_present.str.lower().isin(['yes','y','true','1']) & rules.respect_site_present
    g['candidate_exclusion']=''
    g.loc[target&near,'candidate_exclusion']='Near active/pipeline Airtel site: assess optimization or upgrade'
    g.loc[target&present,'candidate_exclusion']='Grid site-present flag: assess optimization or upgrade'
    g.loc[~target,'candidate_exclusion']='Not an eligible demand grid'
    g['assigned_candidate']='';g['assignment_distance_km']=np.nan
    g['severity_weight']=g.category.map({'Red':rules.red_weight,'Amber':rules.amber_weight}).fillna(0)
    g['weighted_demand']=g.demand.fillna(0)*g.severity_weight*(1+rules.competitor_boost*g.competitor_near)
    from siting import select_locations
    buildings=raw.get('_buildings')
    if buildings is not None:
        w,s,e,n=buildings.bounds
        if e<g.lon.min() or w>g.lon.max() or n<g.lat.min() or s>g.lat.max():
            raise ValueError('Loaded building extent does not overlap the selected grid scope. Load a matching regional building dataset.')
        issues.extend(buildings.issues)
        notes.append(f'{len(buildings.features):,} mapped building footprints used; source: {buildings.metadata.get("source","user dataset")}. Building completeness/age/positional accuracy vary. No building count is converted to population.')
    else:notes.append('No building dataset loaded; footprint clearance/context checks were not performed.')
    sites,points,candidate_assessment=select_locations(g,target,source,blockers,c,rules,reject,notes,log,cancelled,buildings,raw.get('_constraints'))
    g['assigned_demand']=0.0;g['assigned_site_count']=0;g['assigned_fraction']=0.0
    for grid_id,group in points.loc[points.assigned_candidate.ne('')].groupby('grid_id'):
        index=int(group.grid_index.iloc[0]);total=float(group.demand.sum())
        ids=group.assigned_candidate.unique().tolist()
        g.loc[index,'assigned_demand']=total;g.loc[index,'assigned_site_count']=len(ids)
        g.loc[index,'assigned_candidate']=';'.join(ids)
        g.loc[index,'assigned_fraction']=total/float(g.loc[index,'demand'])
        g.loc[index,'assignment_distance_km']=float(np.average(group.distance_km,weights=group.demand))
        g.loc[index,'status']=('Fully assigned opportunity (unverified RF)' if g.loc[index,'assigned_fraction']>=.995 else 'Partially assigned opportunity (unverified RF)')
    assigned=g.assigned_demand.gt(0)
    demand_unit=('people (radius opportunity estimate)' if rules.mode=='Population' else 'MR records (proxy, NOT population or unique users)')
    candidate_count=int(candidate_assessment.rejection.eq('').sum())
    if rules.location_mode!='Supplied candidates':notes.append('Generated coordinates are desktop alternatives, not surveyed or proven buildable sites. Nominal grid envelopes are not actual boundaries.')
    notes.append('Road/backhaul/power distances are straight-line distances to supplied reference POINTS; they are not road routes, fiber routes or line-of-sight checks. Zero constraint limit means disabled.')
    summary={'mode':rules.mode,'country':rules.country,'grid_size':rules.grid_size,
      'grid_period':rules.grid_period,'airtel_period':rules.airtel_period,
      'competitor_periods':sorted(c.period.unique().tolist()),'grid_count':len(g),
      'target_grids':int(target.sum()),'eligible_locations':candidate_count,'location_mode':rules.location_mode,'proposed_sites':len(sites),
      'assigned_grids':int(assigned.sum()),'unassigned_target_grids':int((target & g.assigned_fraction.lt(.995)).sum()),
      'incremental_demand_total':float(g.assigned_demand.sum()),
      'target_demand_total':float(g.loc[target,'demand'].sum()),'demand_unit':demand_unit,
      'population_missing_poor_grids':int((poor&g.population.isna()).sum()) if rules.mode=='Population' else None,
      'invalid_coordinate_rows':len([i for i in issues if 'coordinates' in i['reason']]),
      'notes':notes,'method':'Greedy marginal demand with candidate suitability and distance decay; actual candidate-to-demand distances. No RF propagation prediction.'}
    log(f'Complete: {len(sites)} proposed sites, {int(assigned.sum())} assigned grids.')
    return Plan(sites,g,a,c,pd.DataFrame(issues,columns=['source','source_row','id','reason']),summary,rules,candidate_assessment,points,buildings,raw.get('_constraints'))


def run_paths(paths,rules,mappings=None,log=print,cancelled=lambda:False,source_options=None):
    from database_engine import run_database
    return run_database(paths,rules,mappings,log,cancelled,source_options)



def source_fingerprint(path):
    path=Path(path);files=[]
    if path.suffix.lower()=='.gdbtable' and path.parent.suffix.lower()=='.gdb':
        result=source_fingerprint(path.parent);result['selected_component']=path.name;return result
    if path.is_dir():files=[p for p in path.rglob('*') if p.is_file()]
    elif path.suffix.lower()=='.shp':files=[p for p in path.parent.iterdir() if p.is_file() and p.stem.lower()==path.stem.lower()]
    else:files=[path]
    total=sum(p.stat().st_size for p in files);full=not path.is_dir() and total<=256*1024*1024
    h=hashlib.sha256();manifest=[]
    for item in sorted(files):
        name=str(item.relative_to(path)) if path.is_dir() else item.name;stat=item.stat()
        record={'name':name,'size_bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns};manifest.append(record)
        if full:
            if len(files)>1:h.update(name.encode())
            with item.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    if not full:h.update(json.dumps(manifest,sort_keys=True).encode())
    return {'filename':path.name,'sha256':h.hexdigest(),'fingerprint_method':'source bytes' if full else 'file metadata manifest (not a full byte hash)', 'components':manifest}


def save_plan(plan, root, paths=None):
    """Never overwrite a prior run; keep all provenance and settings with each result."""
    if hasattr(plan,'export'):return plan.export(root,paths)
    folder=Path(root)/('plan_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True,exist_ok=False)
    plan.sites.to_csv(folder/'proposed_sites.csv',index=False)
    grids=plan.grids.rename(columns={'id':'grid_id','size':'grid_size','country':'opco'})
    grids.to_csv(folder/'grid_assessment.csv',index=False)
    plan.candidate_assessment.to_csv(folder/'candidate_location_assessment.csv',index=False)
    plan.demand_points.to_csv(folder/'demand_point_assessment.csv',index=False)
    assignments=plan.demand_points.loc[plan.demand_points.assigned_candidate.ne('')]
    assignments.to_csv(folder/'demand_point_assignments.csv',index=False)
    grouped=assignments.groupby(['assigned_candidate','grid_id'],as_index=False).agg(assigned_demand=('demand','sum'),assigned_points=('point_id','count'))
    grouped.to_csv(folder/'site_grid_assignments.csv',index=False)
    plan.issues.to_csv(folder/'data_quality_issues.csv',index=False)
    grids.loc[grids.candidate_exclusion.str.contains('assess optimization',case=False,na=False)].to_csv(folder/'optimization_review.csv',index=False)
    plan.airtel.to_csv(folder/'airtel_sites_used.csv',index=False)
    plan.competitors.to_csv(folder/'competitor_sites_used.csv',index=False)
    features=[]
    for row in plan.sites.to_dict('records'):
        properties={k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in row.items()}
        features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[row['lon'],row['lat']]},
                         'properties':properties})
    (folder/'proposed_sites.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features},allow_nan=False),encoding='utf-8')
    provenance={key:source_fingerprint(path) for key,path in (paths or {}).items() if path}
    metadata={'rules':asdict(plan.rules),'summary':plan.summary,'sources':provenance,'source_layers_and_crs':plan.input_metadata or {}}
    (folder/'run_settings.json').write_text(json.dumps(metadata,indent=2,allow_nan=False),encoding='utf-8')
    if plan.buildings_context is not None:
        if hasattr(plan.buildings_context,'export_geojson'):plan.buildings_context.export_geojson(folder/'building_footprints_used.geojson')
        else:(folder/'building_footprints_used.geojson').write_text(json.dumps({'type':'FeatureCollection','metadata':plan.buildings_context.metadata,'features':plan.buildings_context.features}),encoding='utf-8')
    if plan.polygon_context is not None:
        from shapely.geometry import mapping
        rows={r['id']:r for r in json.loads(plan.grids.to_json(orient='records'))}
        features=[{'type':'Feature','geometry':mapping(geom),'properties':rows[gid]} for gid,geom in plan.polygon_context.grids.items() if gid in rows]
        (folder/'grid_assessment_spatial.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features},allow_nan=False),encoding='utf-8')
        if plan.polygon_context.exclusions:
            features=[{'type':'Feature','geometry':mapping(geom),'properties':{'id':i+1}} for i,geom in enumerate(plan.polygon_context.exclusions)]
            (folder/'no_build_polygons_used.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8')
    from map_report import write_map
    write_map(plan,folder/'planning_map.html')
    from web_maps import write_web_map,write_earth
    write_web_map(plan,folder/'planning_web_map.html')
    from openlayers_map import write_openlayers_map
    write_openlayers_map(plan,folder/'planning_openlayers_map.html')
    write_earth(plan,folder/'planning_google_earth.kml')
    return folder


def main():
    ap=argparse.ArgumentParser(description='Headless site planning (GUI: python app.py).')
    ap.add_argument('--config',required=True,help='JSON with paths, rules, optional mappings and output_dir')
    ns=ap.parse_args();cfg=json.loads(Path(ns.config).read_text(encoding='utf-8'))
    result=run_paths(cfg['paths'],Rules(**cfg['rules']),cfg.get('mappings'),source_options=cfg.get('source_options'))
    try:print(save_plan(result,cfg.get('output_dir','outputs'),cfg['paths']))
    finally:
        if hasattr(result,'close'):result.close()

if __name__=='__main__':main()
