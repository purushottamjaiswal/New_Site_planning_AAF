"""QGIS entry point. Scientific libraries are not imported into the QGIS process."""
def classFactory(iface):
    from .plugin import NewSitePlanningPlugin
    return NewSitePlanningPlugin(iface)
