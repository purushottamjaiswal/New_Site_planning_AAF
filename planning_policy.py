"""Explicit demand objectives and permitted parent-grid policies."""
TARGETS=('Red + Amber','Red only')
LOCATIONS=('Current behaviour','Target grids only','Red grids only','Any selected grid')
PRIORITIES=('Weighted demand','Red demand first')
def target_category(category,rules):return category=='Red' or (category=='Amber' and rules.target_policy=='Red + Amber')
def candidate_where(rules):
    if rules.location_policy=='Any selected grid':return '1'
    if rules.location_policy=='Red grids only':return "json_extract(payload,'$.category')='Red'"
    return "json_extract(payload,'$.target')=1"
def location_allowed(parent,rules):
    if rules.location_policy=='Red grids only':return parent['category']=='Red'
    if rules.location_policy=='Target grids only':return bool(parent['target'])
    return True
