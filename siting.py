"""Candidate search and screening. No fabricated field-survey accuracy."""
import heapq
import math
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

MODES=('Area search','Supplied candidates','Grid centroid (legacy)','Quick demo (grid centroid)')

def offset_coordinates(lat,lon,east_m,north_m):
    """Spherical direct solution; offsets have local bearing atan2(east,north)."""
    from planner import EARTH_KM
    lat=np.deg2rad(lat);lon=np.deg2rad(lon)
    angle=np.hypot(east_m,north_m)/(1000*EARTH_KM);bearing=np.arctan2(east_m,north_m)
    out_lat=np.arcsin(np.sin(lat)*np.cos(angle)+np.cos(lat)*np.sin(angle)*np.cos(bearing))
    out_lon=lon+np.arctan2(np.sin(bearing)*np.sin(angle)*np.cos(lat),np.cos(angle)-np.sin(lat)*np.sin(out_lat))
    return np.rad2deg(out_lat),(np.rad2deg(out_lon)+180)%360-180


def select_locations(g,target,source,blockers,competitors,rules,reject,notes,log,cancelled,buildings=None,constraints=None):
    from planner import numeric,valid_coords,validate_ids,xyz,chord,km_from_chord,nearest,SITE_COLUMNS
    if rules.building_rule=='Rooftop search' and buildings is None:raise ValueError('Rooftop search requires building footprints.')
    if rules.location_mode not in MODES:raise ValueError('Unknown location mode.')
    if rules.location_mode=='Quick demo (grid centroid)':notes.append('QUICK DEMO: one centroid per target grid; no location search. Approximate coordinates require detailed planning and survey. Search alternative limit does not apply.')
    eligible=g.loc[target].copy()
    side=1000 if rules.grid_size=='1km' else 3000
    # Demand is represented by measured small-area points when supplied. Never add
    # their demand to grid totals: replace the parent grid representation instead.
    points=[]; detailed_grids=set();eligible_ids=set(eligible.id)
    if 'demand_points' in source:
        d=source['demand_points'];d=d.loc[d.country==rules.country.upper()].copy()
        validate_ids(d,'Demand points',['country','id'])
        unknown=d.loc[~d.grid_id.isin(g.id)]
        reject(unknown,'demand_points','Parent grid outside selected scope; ignored')
        d=d.loc[d.grid_id.isin(g.id)].copy()
        original=len(d);d,bad=valid_coords(d)
        if len(bad):raise ValueError('Demand points have invalid coordinates; correct them before siting.')
        d['value']=numeric(d.value)
        if ((~np.isfinite(d.value))|(d.value<0)).any():raise ValueError('Demand point values must be finite and non-negative.')
        lookup=g.set_index('id')
        for gid,group in d.groupby('grid_id',sort=True):
            parent=lookup.loc[gid]
            if gid not in eligible_ids:continue
            total=float(group.value.sum());expected=float(parent.demand)
            if not math.isfinite(expected) or not math.isclose(total,expected,rel_tol=1e-6,abs_tol=.000001):
                raise ValueError(f'Demand point sum for grid {gid} ({total:g}) must match grid demand ({expected:g}) within 0.0001%. Units must match selected mode.')
            coords=np.repeat(xyz(lookup.loc[[gid]]),len(group),axis=0)
            distances=km_from_chord(np.linalg.norm(xyz(group)-coords,axis=1))
            if (distances>side/1000/np.sqrt(2)+.01).any():
                raise ValueError(f'Demand points for {gid} exceed the nominal grid envelope. Check the spatial join.')
            detailed_grids.add(gid)
            for record in group.loc[group.value>0].to_dict('records'):
                points.append({'point_id':record['id'],'grid_id':gid,'lat':record['lat'],'lon':record['lon'],
                  'demand':record['value'],'basis':'Supplied detailed demand point','grid_index':int(parent['_index'])})
    for row in eligible.to_dict('records'):
        if row['id'] not in detailed_grids:
            points.append({'point_id':'GRID:'+row['id'],'grid_id':row['id'],'lat':row['lat'],'lon':row['lon'],
              'demand':row['demand'],'basis':'Coarse grid demand at centroid','grid_index':int(row['_index'])})
    demand=pd.DataFrame(points,columns=['point_id','grid_id','lat','lon','demand','basis','grid_index'])
    demand['assigned_candidate']='';demand['distance_km']=np.nan
    if len(demand):
        idx=demand.grid_index.to_numpy(int)
        # Competitor influence at the demand coordinate, not at a displaced site.
        comp_d,_=nearest(demand,competitors)
        demand['weight']=demand.demand.to_numpy(float)*g.loc[idx,'severity_weight'].to_numpy(float)*(1+rules.competitor_boost*(comp_d<=rules.competitor_radius_km))
    else:demand['weight']=pd.Series(dtype=float)
    notes.append(f'Detailed demand represents {len(detailed_grids)} target grids; {len(eligible)-len(detailed_grids)} use coarse centroid demand. Fine site coordinates do not improve coarse demand resolution.')
    # Construct alternatives. Metadata remains tied to the parent target grid.
    if rules.location_mode=='Supplied candidates':
        if 'candidates' not in source:raise ValueError('Supplied candidates mode requires a candidate CSV. No fallback to grid centroids is performed.')
        cand=source['candidates'];cand=cand.loc[cand.country==rules.country.upper()].copy()
        validate_ids(cand,'Candidate locations',['country','id'])
        cand,bad=valid_coords(cand);reject(bad,'candidates','Invalid candidate coordinates; excluded')
        cand=cand.rename(columns={'id':'source_candidate_id'})
        cand['location_basis']='Supplied candidate coordinate'
        cand['rejection']=''
        cand.loc[~cand.grid_id.isin(g.id),'rejection']='Parent grid outside selected country/resolution/snapshot'
        cand['feasible']=cand.feasible.str.lower()
        cand.loc[~cand.feasible.isin(['yes','true','1']),'rejection']='Candidate feasibility not explicitly confirmed'
        if 'suitability' not in cand:cand['suitability']=1.0
        else:
            cand['suitability']=numeric(cand.suitability.replace('', '1'))
            cand.loc[~cand.suitability.between(0,1),'rejection']='Suitability must be between 0 and 1'
        if 'survey_verified' not in cand:cand['survey_verified']='Unknown'
        if 'accuracy_m' not in cand:cand['accuracy_m']=np.nan
        else:
            original=cand.accuracy_m.astype(str).str.strip()
            cand['accuracy_m']=numeric(original)
            bad_accuracy=original.ne('') & (~np.isfinite(cand.accuracy_m)|cand.accuracy_m.lt(0))
            cand.loc[bad_accuracy,'rejection']='Invalid supplied horizontal survey accuracy'
        if 'location_source' not in cand:cand['location_source']='Not supplied'
    elif rules.building_rule=='Rooftop search':
        cand=buildings.roof_candidates(g)
    else:
        repeat=1
        if rules.location_mode=='Area search':
            n=max(1,int(np.ceil(side/rules.search_spacing_m)))
            offsets=-side/2+(np.arange(n)+.5)*side/n
            offsets=np.array([(e,north) for e in offsets for north in offsets]+[(0.,0.)])
            offsets=np.unique(offsets,axis=0);repeat=len(offsets)
        else:offsets=np.array([[0.,0.]])
        if rules.location_mode!='Quick demo (grid centroid)' and len(eligible)*repeat>rules.max_candidate_points:
            raise ValueError(f'Search generates {len(eligible)*repeat:,} alternatives, above limit {rules.max_candidate_points:,}. Increase search spacing, reduce scope or supply feasible candidates.')
        base=eligible.loc[eligible.index.repeat(repeat)].reset_index(drop=True)
        offsets=np.tile(offsets,(len(eligible),1))
        lat,lon=offset_coordinates(base.lat.to_numpy(float),base.lon.to_numpy(float),offsets[:,0],offsets[:,1])
        cand=pd.DataFrame({'source_candidate_id':[f'SEARCH_{i+1:08d}' for i in range(len(base))],
          'grid_id':base.id,'country':rules.country,'lat':lat,'lon':lon,'feasible':'unknown',
          'suitability':1.,'survey_verified':'No','accuracy_m':np.nan,'location_source':'Generated desktop search',
          'location_basis':('Nominal-grid area search' if rules.location_mode=='Area search' else 'Quick demo grid centroid; approximate location' if rules.location_mode=='Quick demo (grid centroid)' else 'Legacy grid centroid'),
          'rejection':''})
    if rules.location_mode!='Quick demo (grid centroid)' and len(cand)>rules.max_candidate_points:raise ValueError('Candidate count exceeds maximum candidate points.')
    cand=cand.reset_index(drop=True)
    parent_lookup=g.set_index('id')
    cand['parent_grid_index']=cand.grid_id.map(parent_lookup['_index'])
    for field in ['town','category','site_present','lat','lon']:
        cand['parent_'+field]=cand.grid_id.map(parent_lookup[field])
    # Gross mismatch detection uses nominal radius, not a claim of polygon containment.
    if len(cand):
        pcoords=pd.DataFrame({'lat':pd.to_numeric(cand.parent_lat,errors='coerce'),
                              'lon':pd.to_numeric(cand.parent_lon,errors='coerce')})
        cand['offset_from_grid_centroid_m']=km_from_chord(np.linalg.norm(xyz(cand)-xyz(pcoords),axis=1))*1000
        far=cand.offset_from_grid_centroid_m.gt(side/np.sqrt(2)+10) if not(constraints and constraints.grids) else pd.Series(False,index=cand.index)
        cand.loc[far,'rejection']='Outside nominal parent-grid envelope; verify grid assignment'
    else:cand['offset_from_grid_centroid_m']=pd.Series(dtype=float)
    cand['nearest_airtel_km'],ai=nearest(cand,blockers)
    cand['nearest_airtel_id']=blockers.id.to_numpy()[ai] if len(blockers) else ''
    cand.loc[cand.nearest_airtel_km.lt(rules.min_airtel_km),'rejection']='Too near active/reserved Airtel site'
    flagged=cand.parent_site_present.fillna('').str.lower().isin(['yes','true','y','1'])
    if rules.respect_site_present:cand.loc[flagged,'rejection']='Parent grid site-present flag'
    cand['nearest_competitor_km'],ci=nearest(cand,competitors)
    for field in ['id','operator','period']:
        cand['nearest_competitor_'+field]=competitors[field].to_numpy()[ci] if len(competitors) else ''
    if constraints is not None:
        constraints.annotate(cand)
        notes.append(f'Actual GIS grid boundaries: {len(constraints.grids)}; no-build polygons: {len(constraints.exclusions)}. Candidate coordinates checked against polygon topology.')
    if buildings is not None:
        buildings.annotate(cand,rules)
        notes.append(f'Building rule: {rules.building_rule}; footprint counts/area influence location score only. Footprint-derived roof points require structural/access surveys.')
    else:
        for key in ['mapped_buildings_within_radius','mapped_footprint_area_m2','nearest_mapped_building_m','containing_building_area_m2']:cand[key]=np.nan
        cand['containing_building_id']='';cand['building_factor']=1.;cand['building_context']='No building dataset supplied'
    # Exclusion buffers can represent surveyed no-build areas or hazards. Not polygons.
    if 'exclusions' in source:
        ex=source['exclusions'];ex=ex.loc[ex.country==rules.country.upper()].copy()
        ex,bad=valid_coords(ex)
        if len(bad):raise ValueError('No-build buffer coordinates are invalid. Constraints cannot be silently omitted.')
        ex['radius_m']=numeric(ex.radius_m)
        if ((~np.isfinite(ex.radius_m))|ex.radius_m.le(0)).any():raise ValueError('No-build buffer radius_m must be finite and positive.')
        if len(cand):
            tree=cKDTree(xyz(cand))
            for row in ex.to_dict('records'):
                if cancelled():raise InterruptedError('Planning cancelled.')
                matches=tree.query_ball_point(xyz(pd.DataFrame([row]))[0],chord(row['radius_m']/1000))
                cand.loc[matches,'rejection']='No-build buffer: '+row['id']
        notes.append(f'{len(ex)} supplied circular no-build buffers checked; no polygon exclusion layer was provided to this CSV workflow.')
    for kind,label,threshold in [('Road','road',rules.max_road_km),('Backhaul','backhaul',rules.max_backhaul_km),('Power','power',rules.max_power_km)]:
        refs=pd.DataFrame(columns=['lat','lon'])
        if 'infrastructure' in source:
            infra=source['infrastructure'];refs=infra.loc[(infra.country==rules.country.upper())&(infra.kind.str.lower()==kind.lower())].copy()
            refs,bad=valid_coords(refs)
            if len(bad) and threshold>0:raise ValueError(f'Invalid {label} reference coordinates; correct before enforcing this constraint.')
            reject(bad,'infrastructure',f'Invalid {label} reference coordinates')
        if threshold>0 and refs.empty:raise ValueError(f'{label.title()} limit enabled but no valid {kind} infrastructure reference points were supplied.')
        dist,_=nearest(cand,refs);cand['nearest_'+label+'_km']=dist
        if threshold>0:cand.loc[cand['nearest_'+label+'_km'].gt(threshold),'rejection']=f'Beyond {label} reference-point distance limit'
    cand['selected_candidate']='';cand['initial_weighted_benefit']=0.;cand['initial_demand']=0.
    active_idx=np.flatnonzero(cand.rejection.eq(''))
    active=cand.iloc[active_idx]
    if len(active) and len(demand):
        dxyz=xyz(demand);axyz=xyz(active);tree=cKDTree(dxyz)
        neighborhoods=[];factors=[]
        # Limit neighbor storage to prevent an accidental huge-radius memory blow-up.
        total_links=0
        for begin in range(0,len(active),4000):
            if cancelled():raise InterruptedError('Planning cancelled.')
            block=tree.query_ball_point(axyz[begin:begin+4000],chord(rules.service_radius_km))
            total_links+=sum(map(len,block))
            if total_links>10_000_000:raise ValueError('More than 10 million candidate-demand links. Reduce service radius or candidate density.')
            for local,neighbors in enumerate(block):
                indices=np.asarray(neighbors,dtype=int);neighborhoods.append(indices)
                distances=km_from_chord(np.linalg.norm(dxyz[indices]-axyz[begin+local],axis=1))
                # Smooth proximity weighting avoids huge arbitrary jumps within a radius.
                factors.append(np.exp(-rules.distance_decay*(distances/rules.service_radius_km)**2))
    else:neighborhoods=[np.array([],dtype=int) for _ in active_idx];factors=[np.array([]) for _ in active_idx]
    raw=demand.demand.to_numpy(float);weights=demand.weight.to_numpy(float)
    quality=active.suitability.to_numpy(float)*active.building_factor.to_numpy(float)
    initial=[float(np.sum(weights[n]*f)*q) for n,f,q in zip(neighborhoods,factors,quality)]
    cand.loc[active_idx,'initial_weighted_benefit']=initial
    cand.loc[active_idx,'initial_demand']=[float(raw[n].sum()) for n in neighborhoods]
    available=np.ones(len(demand),dtype=bool);allowed=np.ones(len(active_idx),dtype=bool)
    heap=[(-score,j,-1) for j,score in enumerate(initial)];heapq.heapify(heap)
    chosen=[];iteration=0;ctree=cKDTree(xyz(active)) if len(active) else None
    log(f'{len(demand):,} demand points; {len(cand):,} alternatives; {len(active):,} pass siting constraints.')
    while heap and len(chosen)<rules.max_sites:
        if cancelled():raise InterruptedError('Planning cancelled.')
        score,j,stamp=heapq.heappop(heap)
        if not allowed[j]:continue
        n=neighborhoods[j];mask=available[n];fresh=n[mask]
        if not len(fresh) or raw[fresh].sum()<rules.min_incremental:allowed[j]=False;continue
        value=float(np.sum(weights[fresh]*factors[j][mask])*quality[j])
        if value<=0:continue
        if stamp!=iteration:heapq.heappush(heap,(-value,j,iteration));continue
        row=active.iloc[j];rank=len(chosen)+1;cid=f'{rules.country}_{rules.grid_size}_NEW_{rank:04d}'
        distance=km_from_chord(np.linalg.norm(xyz(demand.iloc[fresh])-xyz(active.iloc[[j]])[0],axis=1))
        demand.loc[fresh,'assigned_candidate']=cid;demand.loc[fresh,'distance_km']=distance;available[fresh]=False
        cand.loc[active_idx[j],'selected_candidate']=cid
        parent=g.iloc[int(row.parent_grid_index)]
        chosen.append({'rank':rank,'candidate_id':cid,'country':rules.country,'grid_id':row.grid_id,
          'lat':row.lat,'lon':row.lon,'category':row.parent_category,'town':row.parent_town,
          'incremental_demand':float(raw[fresh].sum()),'weighted_benefit':value,
          'assigned_grids':int(demand.iloc[fresh].grid_id.nunique()),'assigned_points':len(fresh),
          **{k:row[k] for k in ['nearest_airtel_km','nearest_airtel_id','nearest_competitor_km',
           'nearest_competitor_id','nearest_competitor_operator','nearest_competitor_period','nearest_road_km',
           'nearest_backhaul_km','nearest_power_km','location_basis','location_source','survey_verified','accuracy_m',
           'offset_from_grid_centroid_m','source_candidate_id','suitability','mapped_buildings_within_radius','mapped_footprint_area_m2','nearest_mapped_building_m','containing_building_id','containing_building_area_m2','building_factor','building_context']},
          'location_status':('Input feasible candidate; RF and acquisition review required' if rules.location_mode=='Supplied candidates' else 'Quick demo only; approximate grid centroid, detailed planning required' if rules.location_mode=='Quick demo (grid centroid)' else 'Desktop search point; physical feasibility unknown'),
          'demand_basis':('Mixed/coarse grid demand' if (demand.iloc[fresh].basis=='Coarse grid demand at centroid').any() else 'Supplied detailed demand points'),
          'demand_unit':('people (radius opportunity, not predicted coverage)' if rules.mode=='Population' else 'MR records (NOT population)'),
          'reason':f'{row.parent_category} area; {parent.coverage or "unknown"} coverage; {parent.speed or "unknown"} speed; {len(fresh)} incremental demand points'})
        spaced=ctree.query_ball_point(xyz(active.iloc[[j]])[0],chord(rules.min_new_km))
        allowed[spaced]=False;allowed[j]=False;iteration+=1
        if rank%10==0:log(f'Selected {rank} locations.')
    return pd.DataFrame(chosen,columns=SITE_COLUMNS),demand,cand
