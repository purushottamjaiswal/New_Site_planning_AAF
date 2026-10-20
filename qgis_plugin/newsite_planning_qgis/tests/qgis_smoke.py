"""Run manually INSIDE QGIS Python Console after installing the plugin.
from newsite_planning_qgis.tests.qgis_smoke import run
run(iface)
This test uses temporary synthetic geometry and does not add/edit project layers.
"""
def run(iface):
    import csv,json,tempfile
    from pathlib import Path
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsVectorLayer,QgsField,QgsFeature,QgsGeometry,QgsProject
    from ..dock import PlanningDock,SCHEMA
    from ..snapshot import snapshot_layer
    layer=QgsVectorLayer('Polygon?crs=EPSG:3857','Synthetic adapter test','memory')
    provider=layer.dataProvider();names=['opco','grid_id','grid_size','month','combined_category_wf']
    provider.addAttributes([QgsField(n,QVariant.String) for n in names]);layer.updateFields()
    f=QgsFeature(layer.fields());f.setAttributes(['NG','TEST_GRID','1km',"Sep'26",'Red'])
    f.setGeometry(QgsGeometry.fromWkt('POLYGON((0 0,400 0,400 400,0 400,0 0))'));provider.addFeatures([f]);layer.updateExtents()
    rules=dict(SCHEMA['defaults'])
    with tempfile.TemporaryDirectory() as td:
        path,mapping,boundary,info=snapshot_layer(layer,'grid',Path(td)/'grid.csv',SCHEMA['aliases']['grid'],QgsProject.instance(),rules)
        with open(path,newline='',encoding='utf-8') as handle:row=next(csv.DictReader(handle))
        assert 0<float(row['__ns_lat'])<.01
        assert 0<float(row['__ns_lon'])<.01
        assert .1<float(row['__ns_area_km2'])<.2
        assert json.loads(Path(boundary).read_text(encoding='utf-8'))['features'][0]['geometry']['type']=='Polygon'
        assert mapping['lat']=='__ns_lat' and mapping['area']=='__ns_area_km2'
    dock=PlanningDock(iface)
    assert set(dock.controls)==set(SCHEMA['defaults'])
    assert dock.sources['grid']['layer'] is not None
    dock.deleteLater()
    print('QGIS dock creation, projected-polygon snapshot, WGS84 coordinates and ellipsoidal area: PASS')
