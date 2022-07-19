# -*- coding: utf-8 -*-
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    copyright            : (C) 2014-2015 by Sandro Mani / Sourcepole AG
#    email                : smani@sourcepole.ch
"""
/***************************************************************************
 EasyTemplatePrint 

                                 A QGIS plugin
 This plugin makes it easy to print using templates and text variables

 InstantPrintTool courtesy of the above smani@sourcepole.ch
 Adjusted and added functionality by:

                              -------------------
        begin                : 2018-01-08
        git sha              : $Format:%H$
        copyright            : (C) 2018 by Jesper Jøker Eg / GISkonsulenten
        email                : jesper@giskonsulenten.dk
 ***************************************************************************/
"""

from PyQt5 import QtGui, QtCore
from PyQt5.QtCore import *
from PyQt5.QtGui import *

import qgis, platform
from qgis.core import *
from qgis.gui import *
from qgis.PyQt.QtWidgets import *

import os
import math
import uuid
import shapely
import webbrowser
from shapely import affinity
from shapely.geometry import Point, LineString, Polygon
from .PyPDF2.PyPDF2 import PdfFileMerger

from .EasyTemplatePrint_dialog import EasyTemplatePrintDialog


class InstantPrintTool(QgsMapTool):    
  
    def __init__(self, iface, populateCompositionFz=None):
        QgsMapTool.__init__(self, iface.mapCanvas())
        QgsMapToolEmitPoint.__init__(self, iface.mapCanvas())

        self.iface = iface
    
        self.rubberBand = None
        self.rubberband = None
        self.oldrubberband = None
        self.pressPos = None     
           
        projectInstance = QgsProject.instance()
        self.projectLayoutManager = projectInstance.layoutManager()
        
        self.mapitem = None
        self.useLines = False
        
        self.populateCompositionFz = populateCompositionFz
        self.dialog = QDialog(self.iface.mainWindow())
        self.dialogui = EasyTemplatePrintDialog()
        self.dialogui.setupUi(self.dialog)
        self.exportButton = self.dialogui.buttonBox.addButton(self.tr("Export"), QDialogButtonBox.ActionRole)
        self.exportButton.setDefault(True)
        self.helpButton = self.dialogui.buttonBox.addButton(self.tr("Help"), QDialogButtonBox.HelpRole)
        self.helpButton.setEnabled(True)
        self.dialogui.comboBox_fileformat.addItem("PDF", self.tr("PDF Document (*.pdf);;"))
        self.dialogui.comboBox_fileformat.addItem("JPG", self.tr("JPG Image (*.jpg);;"))
        self.dialogui.comboBox_fileformat.addItem("BMP", self.tr("BMP Image (*.bmp);;"))
        self.dialogui.comboBox_fileformat.addItem("PNG", self.tr("PNG Image (*.png);;"))
        self.iface.layoutDesignerOpened.connect(lambda view: self.__reloadLayouts())
        self.iface.layoutDesignerWillBeClosed.connect(self.__reloadLayouts)
        self.dialogui.spinBoxScale.valueChanged.connect(self.__changeScale)
        self.dialogui.spinBoxRotation.valueChanged.connect(self.__changeRotation)
        self.dialogui.comboBox_composers.currentIndexChanged.connect(self.__selectComposer)  
        self.dialogui.pushButtonMapcanvasScale.clicked.connect(self.__useCanvasScale)
        self.dialogui.pushButtonPrintAlongLine.clicked.connect(self.__printAlongLine)
        self.dialogui.checkBoxPrintAlongLine.stateChanged.connect(self.__usePrintAlong)
        self.exportButton.clicked.connect(self.__export)
        self.helpButton.clicked.connect(self.__help)
        self.dialogui.buttonBox.button(QDialogButtonBox.Close).clicked.connect(lambda: self.setEnabled(False))
        self.deactivated.connect(self.__cleanup)
        self.setCursor(Qt.OpenHandCursor)
        self.isEmittingPoint = False
        
    def setEnabled(self, enabled):
        if enabled:
            self.dialog.setVisible(True)
            self.__reloadLayouts()
            self.__selectLayout()
            self.iface.mapCanvas().setMapTool(self)
        else:
            self.dialog.setVisible(False)
            self.__cleanup()
            self.iface.mapCanvas().unsetMapTool(self)

    def __changeRotation(self):
        if not self.mapitem:
            return
        self.mapitem.setMapRotation(self.dialogui.spinBoxRotation.value())
        self.__createRubberBand()
        
    def __useCanvasScale(self):
        self.dialogui.spinBoxScale.setValue(int(round(self.iface.mapCanvas().scale()/10,1)*10))
        
    def __usePrintAlong(self):
        self.__cleanup()
        self.isEmittingPoint = False
        #self.isDrawing = False
        if self.dialogui.checkBoxPrintAlongLine.isChecked():
            self.dialogui.pushButtonPrintAlongLine.setEnabled(True)
            self.dialogui.comboBox_composers.setEnabled(False)
            self.dialogui.comboBox_fileformat.setEnabled(False)
            self.dialogui.spinBoxScale.setEnabled(False)
            self.dialogui.spinBoxRotation.setEnabled(False)
            self.dialogui.pushButtonMapcanvasScale.setEnabled(False)
            self.dialogui.overlapLabel.setEnabled(True)
            self.dialogui.spinBoxOverlap.setEnabled(True)
            self.useLines = True
            if self.dialogui.comboBox_fileformat.itemData(self.dialogui.comboBox_fileformat.currentIndex()).lower() != 'pdf document (*.pdf);;':
                QMessageBox.information(None, "ERROR:", "Only PDF is valid for multiple files output format.")
        if not self.dialogui.checkBoxPrintAlongLine.isChecked():
            self.dialogui.pushButtonPrintAlongLine.setEnabled(False)
            self.dialogui.comboBox_composers.setEnabled(True)
            self.dialogui.comboBox_fileformat.setEnabled(True)
            self.dialogui.spinBoxScale.setEnabled(True)
            self.dialogui.spinBoxRotation.setEnabled(True)
            self.dialogui.pushButtonMapcanvasScale.setEnabled(True)
            self.dialogui.overlapLabel.setEnabled(False)
            self.dialogui.spinBoxOverlap.setEnabled(False)
            self.useLines = False
            self.__cleanup()
            self.__reloadLayouts()
            
    def __printAlongLine(self):
        self.__cleanup()
        self.isEmittingPoint = False
        #self.isDrawing = False
        self.defineRubberBand()
        self.iface.mapCanvas().setCursor(Qt.CrossCursor)

    def __changeScale(self):
        if not self.mapitem:
            return
        newscale = self.dialogui.spinBoxScale.value()
        if abs(newscale) < 1E-6:
            return
        extent = self.mapitem.extent()
        center = extent.center()
        newwidth = extent.width() / self.mapitem.scale() * newscale
        newheight = extent.height() / self.mapitem.scale() * newscale
        x1 = center.x() - 0.5 * newwidth
        y1 = center.y() - 0.5 * newheight
        x2 = center.x() + 0.5 * newwidth
        y2 = center.y() + 0.5 * newheight
        self.mapitem.setExtent(QgsRectangle(x1, y1, x2, y2))
        self.__createRubberBand()
                
    def __createRubberBand(self):
        if not self.useLines:
            self.__cleanup()
            extent = self.mapitem.extent()
            self.width = extent.width()
            center = self.iface.mapCanvas().extent().center()
            self.corner = QPointF(center.x() - 0.5 * extent.width(), center.y() - 0.5 * extent.height())
            self.rect = QRectF(self.corner.x(), self.corner.y(), extent.width(), extent.height())
            self.mapitem.setExtent(QgsRectangle(self.rect))

            self.__createRubberbandAsGeometry()
               
            self.pressPos = None

    def __cleanup(self):
        if self.rubberband:
            self.iface.mapCanvas().scene().removeItem(self.rubberband)
        if self.oldrubberband:
            self.iface.mapCanvas().scene().removeItem(self.oldrubberband)
        if self.rubberBand:
            self.iface.mapCanvas().scene().removeItem(self.rubberBand)
        self.rubberband = None
        self.rubberBand = None
        self.oldrubberband = None
        self.pressPos = None

    def defineRubberBand(self):
        self.rubberBand = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
        self.rubberBand.setColor(QColor(127, 127, 255, 127))
        self.rubberBand.setWidth(2)        

    def canvasPressEvent(self, e):
        if not self.useLines:
            if not self.rubberband:
                return
            r = self.__canvasRect(self.rect)
            posMap = QgsPoint(self.toMapCoordinates(e.pos()).x(), self.toMapCoordinates(e.pos()).y())
            pos = QPointF(posMap.x(), posMap.y())	
        
            if e.button() == Qt.LeftButton and self.__canvasRect(self.rect).contains(pos):
                self.pressPos = (pos.x(), pos.y())
                self.iface.mapCanvas().setCursor(Qt.ClosedHandCursor)
                    
    def reset(self):
        if self.rubberBand:
            self.rubberBand.reset(QgsWkbTypes.LineGeometry)
        self.isEmittingPoint = False
        self.defineRubberBand()
                    
    def canvasMoveEvent(self, e):
        if not self.useLines:
            if not self.pressPos:
                return
            mapPoint = self.toMapCoordinates(e.pos())
            x = self.corner.x() + (mapPoint.x() - self.pressPos[0]) #* mup
            y = self.corner.y() + (mapPoint.y() - self.pressPos[1]) #* mup

            self.rect = QRectF(
                x,
                y,
                self.rect.width(),
                self.rect.height()
                )
            self.__createRubberbandAsGeometry()
            
        if self.useLines:
            if self.isEmittingPoint and self.rubberBand:
                self.rubberBand.movePoint(self.toMapCoordinates(e.pos()))
        
    def __createRubberbandAsGeometry(self):
        if not self.useLines:
            lTopx = self.__canvasRect(self.rect).x()
            lTopy = self.__canvasRect(self.rect).y() + self.__canvasRect(self.rect).height()
            rTopx = self.__canvasRect(self.rect).x() + self.__canvasRect(self.rect).width()
            rTopy = self.__canvasRect(self.rect).y() + self.__canvasRect(self.rect).height()
            rBotx = self.__canvasRect(self.rect).x() + self.__canvasRect(self.rect).width()
            rBoty = self.__canvasRect(self.rect).y()
            lBotx = self.__canvasRect(self.rect).x()
            lBoty = self.__canvasRect(self.rect).y()
            polygon = Polygon(((lBotx, lBoty), (lTopx, lTopy), (rTopx, rTopy), (rBotx, rBoty)))
        
            if self.rubberband:
                self.iface.mapCanvas().scene().removeItem(self.rubberband)
        
            if self.dialogui.spinBoxRotation.value()>0:
                rotatedPolygon = shapely.affinity.rotate(polygon, self.dialogui.spinBoxRotation.value(), origin='centroid', use_radians=False)
                x,y = rotatedPolygon.exterior.coords.xy
                points = [[QgsPointXY(x[0],y[0]), QgsPointXY(x[1], y[1]), QgsPointXY(x[2], y[2]), QgsPointXY(x[3], y[3])]]
                self.rubberband = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
                self.rubberband.setToGeometry(QgsGeometry.fromPolygonXY(points), None)
                self.rubberband.setColor(QColor(127, 127, 255, 127))
        
            if self.dialogui.spinBoxRotation.value()==0:
                points = [[QgsPointXY(lBotx, lBoty), QgsPointXY(lTopx, lTopy), QgsPointXY(rTopx, rTopy), QgsPointXY(rBotx, rBoty)]]
                self.rubberband = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
                self.rubberband.setToGeometry(QgsGeometry.fromPolygonXY(points), None)
                self.rubberband.setColor(QColor(127, 127, 255, 127))
               
    def canvasReleaseEvent(self, e):
        if not self.useLines:
            if e.button() == Qt.LeftButton and self.pressPos:
                self.corner = QPointF(self.rect.x(), self.rect.y())
                self.pressPos = None
                self.iface.mapCanvas().setCursor(Qt.OpenHandCursor)
                self.iface.mapCanvas().scene().removeItem(self.oldrubberband)
                self.oldrect = None
                self.oldrubberband = None
                self.mapitem.setExtent(QgsRectangle(self.rect))
                
        if self.useLines:
            if e.button() == Qt.LeftButton:
                posMap = QgsPoint(self.toMapCoordinates(e.pos()).x(), self.toMapCoordinates(e.pos()).y())
                pos = QPointF(posMap.x(), posMap.y())	
                self.pressPos = (pos.x(), pos.y())
                self.isEmittingPoint = True
                if self.isEmittingPoint:
                    point = self.toMapCoordinates(e.pos())
                    if self.rubberBand: 
                        self.rubberBand.addPoint(point, True)
                else:
                    self.reset()

            if e.button() == Qt.RightButton and self.isEmittingPoint:
                self.isEmittingPoint = False
                self.rubberBand.removeLastPoint()
                self.iface.mapCanvas().setCursor(Qt.ArrowCursor)
                            
    def __canvasRect(self, rect):
        p1 = QgsPoint(rect.left(), rect.top())
        p2 = QgsPoint(rect.right(), rect.bottom())
        return QRectF(p1.x(), p1.y(), p2.x() - p1.x(), p2.y() - p1.y())

        
    def __selectComposer(self):
        chk = ' []@%'
        labels = []    
        
        if not self.dialog.isVisible():
            return

        activeIndex = self.dialogui.comboBox_composers.currentIndex()
        if activeIndex < 0:
            return
        
        composerView = self.dialogui.comboBox_composers.itemData(activeIndex)
        maps = []
        self.layout_name = self.dialogui.comboBox_composers.currentText()
        self.layout = self.projectLayoutManager.layoutByName(self.layout_name)
        
        for item in composerView.items():
            if isinstance(item, QgsLayoutItemMap):
                maps.append(item)
                        
        if len(maps) != 1:
            QMessageBox.information(self.iface.mainWindow(), self.tr("Invalid composer"), self.tr("The composer must have exactly one map item."))
            self.exportButton.setEnabled(False)
            self.dialogui.spinBoxScale.setEnabled(False)
            self.dialogui.spinBoxRotation.setEnabled(False)
            self.dialogui.LegendCheckbox.setEnabled(False)
            return
        
        self.dialogui.spinBoxScale.setEnabled(True)
        self.dialogui.spinBoxRotation.setEnabled(True)        
        self.exportButton.setEnabled(True)
        self.dialogui.LegendCheckbox.setEnabled(False)
        for item in composerView.items():
            if isinstance(item,QgsLayoutItemLegend):
                self.dialogui.LegendCheckbox.setEnabled(True)
        
        self.composerView = composerView
        self.mapitem = maps[0]
        self.dialogui.spinBoxScale.setValue(int(round(self.mapitem.scale()/10,1)*10))
        self.mapitem.setMapRotation(0)
        
        def containsAll(str,set):
            return 0 not in [c in str for c in set]
            
        stdVars = ['qgis_os_name','qgis_platform','qgis_release_name','qgis_version','qgis_version_no','user_account_name','user_full_name','project_filename','project_folder','project_title']
        for item in composerView.items():
            if isinstance(item,QgsLayoutItemLabel):
                if containsAll(item.text(),chk)!=0 and "\n" not in item.text():
                    if not any(item.text().strip('[]\@% ') in x  for x in stdVars):
                        labels.append(item.text().strip('[]\@% ').rstrip())

        lineEditsList = [self.dialogui.lineEdit1,self.dialogui.lineEdit2,self.dialogui.lineEdit3,self.dialogui.lineEdit4,self.dialogui.lineEdit5]
        labelList =[self.dialogui.label_1,self.dialogui.label_2,self.dialogui.label_3,self.dialogui.label_4,self.dialogui.label_5]
        for l in lineEditsList:
            l.clear()
            l.setVisible(False)
        for l in labelList:
            l.clear()
            l.setVisible(False)
        count = len(labels) 
        if count==1:
            self.dialogui.lineEdit1.setText(labels[0])
            self.dialogui.lineEdit1.setVisible(True)
            self.dialogui.lineEdit2.setVisible(False)
            self.dialogui.lineEdit3.setVisible(False)
            self.dialogui.lineEdit4.setVisible(False)
            self.dialogui.lineEdit5.setVisible(False)
            self.dialogui.label_1.setText(labels[0] + ':')
            self.dialogui.label_1.setVisible(True)
            self.dialogui.label_2.setVisible(False)
            self.dialogui.label_3.setVisible(False)
            self.dialogui.label_4.setVisible(False)
            self.dialogui.label_5.setVisible(False)
            
        if count==2:
            self.dialogui.lineEdit1.setText(labels[0])
            self.dialogui.lineEdit2.setText(labels[1])
            self.dialogui.lineEdit1.setVisible(True)
            self.dialogui.lineEdit2.setVisible(True)
            self.dialogui.lineEdit3.setVisible(False)
            self.dialogui.lineEdit4.setVisible(False)
            self.dialogui.lineEdit5.setVisible(False)
            self.dialogui.label_1.setText(labels[0] + ':')
            self.dialogui.label_2.setText(labels[1] + ':')
            self.dialogui.label_1.setVisible(True)
            self.dialogui.label_2.setVisible(True)
            self.dialogui.label_3.setVisible(False)
            self.dialogui.label_4.setVisible(False)
            self.dialogui.label_5.setVisible(False)
        if count==3:            
            self.dialogui.lineEdit1.setText(labels[0])
            self.dialogui.lineEdit2.setText(labels[1])
            self.dialogui.lineEdit3.setText(labels[2])
            self.dialogui.lineEdit1.setVisible(True)
            self.dialogui.lineEdit2.setVisible(True)
            self.dialogui.lineEdit3.setVisible(True)
            self.dialogui.lineEdit4.setVisible(False)
            self.dialogui.lineEdit5.setVisible(False)
            self.dialogui.label_1.setText(labels[0] + ':')
            self.dialogui.label_2.setText(labels[1] + ':')
            self.dialogui.label_3.setText(labels[2] + ':')
            self.dialogui.label_1.setVisible(True)
            self.dialogui.label_2.setVisible(True)
            self.dialogui.label_3.setVisible(True)
            self.dialogui.label_4.setVisible(False)
            self.dialogui.label_5.setVisible(False)
        if count==4:
            self.dialogui.lineEdit1.setText(labels[0])
            self.dialogui.lineEdit2.setText(labels[1])
            self.dialogui.lineEdit3.setText(labels[2])
            self.dialogui.lineEdit4.setText(labels[3])
            self.dialogui.lineEdit1.setVisible(True)
            self.dialogui.lineEdit2.setVisible(True)
            self.dialogui.lineEdit3.setVisible(True)
            self.dialogui.lineEdit4.setVisible(True)
            self.dialogui.lineEdit5.setVisible(False)
            self.dialogui.label_1.setText(labels[0] + ':')
            self.dialogui.label_2.setText(labels[1] + ':')
            self.dialogui.label_3.setText(labels[2] + ':')
            self.dialogui.label_4.setText(labels[3] + ':')
            self.dialogui.label_1.setVisible(True)
            self.dialogui.label_2.setVisible(True)
            self.dialogui.label_3.setVisible(True)
            self.dialogui.label_4.setVisible(True)
            self.dialogui.label_5.setVisible(False)
        if count==5:
            self.dialogui.lineEdit1.setText(labels[0])
            self.dialogui.lineEdit2.setText(labels[1])
            self.dialogui.lineEdit3.setText(labels[2])
            self.dialogui.lineEdit4.setText(labels[3])
            self.dialogui.lineEdit5.setText(labels[4])
            self.dialogui.lineEdit1.setVisible(True)
            self.dialogui.lineEdit2.setVisible(True)
            self.dialogui.lineEdit3.setVisible(True)
            self.dialogui.lineEdit4.setVisible(True)
            self.dialogui.lineEdit5.setVisible(True)
            self.dialogui.label_1.setText(labels[0] + ':')
            self.dialogui.label_2.setText(labels[1] + ':')
            self.dialogui.label_3.setText(labels[2] + ':')
            self.dialogui.label_4.setText(labels[3] + ':')
            self.dialogui.label_5.setText(labels[4] + ':')
            self.dialogui.label_1.setVisible(True)
            self.dialogui.label_2.setVisible(True)
            self.dialogui.label_3.setVisible(True)
            self.dialogui.label_4.setVisible(True)
            self.dialogui.label_5.setVisible(True)
        
        self.__createRubberBand()
    
    def __selectLayout(self):
        if not self.dialog.isVisible():
            return
        activeIndex = self.dialogui.comboBox_composers.currentIndex()
        if activeIndex < 0:
            return

        layoutView = self.dialogui.comboBox_composers.itemData(activeIndex)
        maps = []
        self.layout_name = self.dialogui.comboBox_composers.currentText()
        self.layout = self.projectLayoutManager.layoutByName(self.layout_name)
        self.dialogui.LegendCheckbox.setEnabled(False)
        self.dialogui.LegendCheckbox.setChecked(True)
        for item in layoutView.items():
            if isinstance(item, QgsLayoutItemMap):
                maps.append(item)
            if isinstance(item,QgsLayoutItemLegend):
                self.dialogui.LegendCheckbox.setEnabled(True)
                self.dialogui.LegendCheckbox.setChecked(True)
                
        if len(maps) != 1:
            QMessageBox.information(self.iface.mainWindow(), self.tr("Invalid layout"), self.tr("The layout must have exactly one map item."))
            self.exportButton.setEnabled(False)
            self.iface.mapCanvas().scene().removeItem(self.rubberband)
            self.rubberband = None
            self.dialogui.comboBox_scale.setEnabled(False)
            return

        self.dialogui.spinBoxScale.setEnabled(True)
        self.exportButton.setEnabled(True)
        self.layoutView = layoutView
        self.mapitem = self.layout.referenceMap()
        self.__createRubberBand()
        
  
    def __export(self):
        if not self.useLines:
            self.__exportSingle()
        if self.useLines:
            if not self.rubberBand:
                QMessageBox.information(None, "Error:", "No line present in map") 
                return
            self.prepareMultipleMapsForExport()
            
    
    
    def prepareMultipleMapsForExport(self): 
        vertexCount = self.rubberBand.numberOfVertices()
        segmentCount = vertexCount - 1 
        
        overlapInMeters =  (self.dialogui.spinBoxOverlap.value()/100 * self.dialogui.spinBoxScale.value() )/ 2    #(self.dialogui.spinBoxOverlap.value()/100)/(1/self.dialogui.spinBoxScale.value())/2
        
        TestwidthOfMap = self.mapitem.extent().width() - (2*overlapInMeters)
        widthOfMap = self.mapitem.extent().width()
        heightOfMap = self.mapitem.extent().height()
        
        if overlapInMeters>(widthOfMap/2):
            QMessageBox.information(None, "Error:", "Overlap too large for this type of page.") 
            return
        self.fileList = []
        settings = QSettings()
        
        format = self.dialogui.comboBox_fileformat.itemData(self.dialogui.comboBox_fileformat.currentIndex())
        self.filepath = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            self.tr("Export Layout"),
            settings.value("/instantprint/lastfile", ""),
            format
        )
        
        if not self.filepath:
            self.__cleanup()
            return
        
        if os.path.isfile(self.filepath[0]):
            os.remove(self.filepath[0])
        
        settings.setValue("/instantprint/lastfile", self.filepath[0])
        v = 0
        vList = []
        while v < vertexCount:
            vertex = self.rubberBand.getPoint(0,v)
            vList.append([vertex.x(), vertex.y()])
            v += 1
        
        s = 0
        while s < segmentCount:
            p1 = QgsPoint(vList[s][0],vList[s][1])
            p2 = QgsPoint(vList[s+1][0],vList[s+1][1])
            angle = (90 - math.degrees(QgsGeometryUtils.lineAngle(vList[s][0], vList[s][1], vList[s+1][0], vList[s+1][1])))
            lengthOfSegment = math.sqrt((vList[s][0]-vList[s+1][0])**2 + (vList[s][1]-vList[s+1][1])**2)
            if lengthOfSegment <= widthOfMap:
                newPoint = (QgsGeometryUtils.midpoint(p1,p2)) 
                x1 = newPoint.x() - 0.5 * widthOfMap
                y1 = newPoint.y() - 0.5 * heightOfMap
                x2 = newPoint.x() + 0.5 * widthOfMap
                y2 = newPoint.y() + 0.5 * heightOfMap
                self.mapitem.setExtent(QgsRectangle(x1, y1, x2, y2))
                self.mapitem.setMapRotation(angle)
                self.__exportMultiple()
            else:
                divisions = abs(lengthOfSegment/TestwidthOfMap)
                counterDivisions = 0
                while counterDivisions < divisions:
                    if counterDivisions == 0:
                        newPoint = QgsGeometryUtils.pointOnLineWithDistance(p1, p2, TestwidthOfMap/2)
                    else:
                        newPoint=QgsGeometryUtils.pointOnLineWithDistance(newPoint, p2, TestwidthOfMap)
                    x1 = newPoint.x() - 0.5 * widthOfMap
                    y1 = newPoint.y() - 0.5 * heightOfMap
                    x2 = newPoint.x() + 0.5 * widthOfMap
                    y2 = newPoint.y() + 0.5 * heightOfMap
                    self.mapitem.setExtent(QgsRectangle(x1, y1, x2, y2))
                    self.mapitem.setMapRotation(angle)
                    counterDivisions +=1
                    self.__exportMultiple()         
            s += 1
            
        merger = PdfFileMerger()

        for pdf in self.fileList:  
            merger.append(pdf)

        if self.filepath[0]:
            with open(self.filepath[0], 'wb') as f:
                merger.write(f)
        
        merger.close()
        
        for f in self.fileList:
            if os.path.isfile(f):
                os.remove(f)
        if self.filepath[0]:       
            box = QMessageBox()
            box.setIcon(QMessageBox.Information)
            box.setText("Finished export to file: \n\n" + self.filepath[0] + '\n\nOpen file?')
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            buttonYes = box.button(QMessageBox.Yes)
            buttonYes.setText('Continue')
            buttonNo = box.button(QMessageBox.No)
            buttonNo.setText('Cancel')
            box.exec_()
        
            if box.clickedButton() == buttonYes:
                webbrowser.open_new(r'file://' + self.filepath[0] )
            
                
    def __exportMultiple(self):
        settings = QSettings()
 
        labelName = [self.dialogui.label_1.text().strip(':'),self.dialogui.label_2.text().strip(':'),self.dialogui.label_3.text().strip(':'),self.dialogui.label_4.text().strip(':'),self.dialogui.label_5.text().strip(':')] 
        labelText = [self.dialogui.lineEdit1.text(),self.dialogui.lineEdit2.text(),self.dialogui.lineEdit3.text(),self.dialogui.lineEdit4.text(),self.dialogui.lineEdit5.text()]  
        idx = 0
        project = QgsProject.instance()
        for l in labelName:
            QgsExpressionContextUtils.setProjectVariable(project,l,labelText[idx])
            idx = idx + 1
         
        if self.populateCompositionFz:
            self.populateCompositionFz(self.composerView.composition())
 
        success = False
        self.layout_name = self.dialogui.comboBox_composers.currentText()
        self.layout_item = self.projectLayoutManager.layoutByName(self.layout_name)
   
        for item in self.layout_item.items():
            if isinstance(item,QgsLayoutItemLegend):
                if self.dialogui.LegendCheckbox.isChecked() and self.dialogui.LegendCheckbox.isEnabled():
                    item.setExcludeFromExports(False)
                else:
                    item.setExcludeFromExports(True)     
        
        fullPath = str(os.path.dirname(self.filepath[0]) + os.path.sep + str(uuid.uuid4()) + ".pdf") 
           
        exporter = QgsLayoutExporter(self.layout_item)
        success = exporter.exportToPdf(fullPath, QgsLayoutExporter.PdfExportSettings())
          
        if self.filepath[0]:  
            self.fileList.append(fullPath)  
        
        for item in self.layout_item.items():
            if isinstance(item,QgsLayoutItemLegend):
                item.setExcludeFromExports(False)
                    
    def __exportSingle(self):
        settings = QSettings()
        labelName = [self.dialogui.label_1.text().strip(':'),self.dialogui.label_2.text().strip(':'),self.dialogui.label_3.text().strip(':'),self.dialogui.label_4.text().strip(':'),self.dialogui.label_5.text().strip(':')] 
        labelText = [self.dialogui.lineEdit1.text(),self.dialogui.lineEdit2.text(),self.dialogui.lineEdit3.text(),self.dialogui.lineEdit4.text(),self.dialogui.lineEdit5.text()]  
        idx = 0
        project = QgsProject.instance()
        for l in labelName:
            QgsExpressionContextUtils.setProjectVariable(project,l,labelText[idx])
            idx = idx + 1
                
        if self.populateCompositionFz:
            self.populateCompositionFz(self.composerView.composition())
        
        success = False
        self.layout_name = self.dialogui.comboBox_composers.currentText()
        self.layout_item = self.projectLayoutManager.layoutByName(self.layout_name)
          
        for item in self.layout_item.items():
            if isinstance(item,QgsLayoutItemLegend):
               if self.dialogui.LegendCheckbox.isChecked() and self.dialogui.LegendCheckbox.isEnabled():
                   item.setExcludeFromExports(False)
               else:
                   item.setExcludeFromExports(True)     
        
        format = self.dialogui.comboBox_fileformat.itemData(self.dialogui.comboBox_fileformat.currentIndex())
        self.filepath = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            self.tr("Export Layout"),
            settings.value("/instantprint/lastfile", ""),
            format
        )
          
        if not self.filepath:
            self.__cleanup()
            return  

        filename = os.path.splitext(self.filepath[0])[0] + "." + self.dialogui.comboBox_fileformat.currentText().lower()
        settings.setValue("/instantprint/lastfile", self.filepath[0])
        
        exporter = QgsLayoutExporter(self.layout_item)
        if filename[-3:].lower() == "pdf":
            success = exporter.exportToPdf(self.filepath[0], QgsLayoutExporter.PdfExportSettings())
        else:
            success = exporter.exportToImage(self.filepath[0], QgsLayoutExporter.ImageExportSettings())
        if success != 0:
            QMessageBox.warning(self.iface.mainWindow(), self.tr("Export Failed"), self.tr("Failed to export the layout."))
        else:
            box = QMessageBox()
            box.setIcon(QMessageBox.Information)
            box.setText("Finished export to file: \n\n" + filename + '\n\nOpen file?')
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            buttonYes = box.button(QMessageBox.Yes)
            buttonYes.setText('Continue')
            buttonNo = box.button(QMessageBox.No)
            buttonNo.setText('Cancel')
            box.exec_()
            if box.clickedButton() == buttonYes:
                webbrowser.open_new(r'file://' + filename )
                
        for item in self.layout_item.items():
            if isinstance(item,QgsLayoutItemLegend):
                item.setExcludeFromExports(False)
                   
    def __reloadLayouts(self, removed=None):
        if not self.dialog.isVisible():
            return

        self.dialogui.comboBox_composers.blockSignals(True)
        prev = None
        if self.dialogui.comboBox_composers.currentIndex() >= 0:
            prev = self.dialogui.comboBox_composers.currentText()
        self.dialogui.comboBox_composers.clear()
        active = 0
        for layout in self.projectLayoutManager.layouts():
            if layout != removed and layout.name():
                cur = layout.name()
                self.dialogui.comboBox_composers.addItem(cur, layout)
                if prev == cur:
                    active = self.dialogui.comboBox_composers.count() - 1
        self.dialogui.comboBox_composers.setCurrentIndex(-1)  
        self.dialogui.comboBox_composers.blockSignals(False)
        if self.dialogui.comboBox_composers.count() > 0:
            self.dialogui.comboBox_composers.setCurrentIndex(active)
            self.dialogui.spinBoxScale.setEnabled(True)
            self.exportButton.setEnabled(True)
        else:
            self.exportButton.setEnabled(False)
            self.dialogui.spinBoxScale.setEnabled(False)

    def __help(self):
        manualPath = os.path.join(os.path.dirname(__file__), "help", "documentation.pdf")
        QDesktopServices.openUrl(QUrl.fromLocalFile(manualPath))
