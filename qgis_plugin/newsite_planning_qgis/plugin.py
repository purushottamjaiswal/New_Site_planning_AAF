"""Lightweight QGIS lifecycle; no scientific imports at plugin scan or load time."""
from pathlib import Path
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt

class NewSitePlanningPlugin:
    def __init__(self,iface):self.iface=iface;self.action=None;self.dock=None
    def initGui(self):
        self.action=QAction(QIcon(str(Path(__file__).with_name('icon.svg'))),'New Site Planning',self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu('&New Site Planning',self.action);self.iface.addToolBarIcon(self.action)
    def show(self):
        if self.dock is None:
            from .dock import PlanningDock
            self.dock=PlanningDock(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea,self.dock)
        self.dock.show();self.dock.raise_()
    def unload(self):
        if self.dock is not None:
            self.dock.shutdown();self.iface.removeDockWidget(self.dock);self.dock.deleteLater();self.dock=None
        if self.action is not None:
            self.iface.removePluginMenu('&New Site Planning',self.action);self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater();self.action=None
