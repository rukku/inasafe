"""
InaSAFE Disaster risk assessment tool developed by AusAid -
**Batch runner dialog.**

Contact : ole.moller.nielsen@gmail.com

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

"""

__author__ = 'bungcip@gmail.com & tim@linfiniti.com'
__revision__ = '$Format:%H$'
__date__ = '01/10/2012'
__copyright__ = ('Copyright 2012, Australia Indonesia Facility for '
                 'Disaster Reduction')

import os
import sys
import logging

from StringIO import StringIO
from ConfigParser import ConfigParser, MissingSectionHeaderError

from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import (pyqtSignature, QSettings, QVariant, QString, Qt)
from PyQt4.QtGui import (QDialog, QFileDialog, QTableWidgetItem, QMessageBox)

from qgis.core import QgsRectangle

from safe_qgis.batch_runner_base import Ui_BatchRunnerBase
from safe_qgis.batch_option import BatchOption

from safe_qgis.map import Map
from safe_qgis.html_renderer import HtmlRenderer
from safe_qgis.exceptions import QgisPathError
from safe_qgis.safe_interface import temp_dir

from safe_qgis import macro
from safe_qgis.utilities import safeTr
from safe_qgis.exceptions import KeywordNotFoundError

LOGGER = logging.getLogger('InaSAFE')


class BatchRunner(QDialog, Ui_BatchRunnerBase):
    """Script Dialog for InaSAFE."""

    def __init__(self, theParent=None, theIface=None):
        """Constructor for the dialog.

        Args:
           theParent - Optional widget to use as parent.
        Returns:
           not applicable
        Raises:
           no exceptions explicitly raised
        """
        QDialog.__init__(self, theParent)
        self.setupUi(self)

        self.iface = theIface
        myRoot = os.path.dirname(__file__)
        self.defaultSourceDir = os.path.abspath(
            os.path.join(myRoot, '..', 'script_runner'))
        self.sourceDir = self.defaultSourceDir
        self.ignoreBaseDataDir = False

        # initialized task list view
        self.model = TaskModel(self)
        self.itemDelegate = TaskItemDelegate(self)

        self.lvTask.setModel(self.model)
        self.lvTask.setItemDelegate(self.itemDelegate)

        #self.adjustSize()
        self.restoreState()

        self.populate(self.sourceDir)

        # setup signal & slot
        self.pleSourcePath.lePath.textChanged.connect(self.populate)
        self.itemDelegate.runClicked.connect(self.runTask)
        self.pbnRunAll.clicked.connect(self.runAllTask)
        self.pbnOption.clicked.connect(self.showOptionDialog)

    def populate(self, theBasePath):
        self.model.populate(theBasePath, self.sourceDir)

    def restoreState(self):
        """Restore GUI state from configuration file"""

        mySettings = QSettings()

        self.baseDataDir = mySettings.value('inasafe/baseDataDir',
                                            QString('')).toString()
        self.sourceDir = mySettings.value('inasafe/lastSourceDir',
                                          self.defaultSourceDir).toString()
        self.reportDir = mySettings.value('inasafe/reportDir',
                                          self.baseDataDir).toString()
        self.ignoreBaseDataDir = mySettings.value(
            'inasafe/ignoreBaseDataDir',
            self.ignoreBaseDataDir).toBool()

        self.pleSourcePath.setText(self.sourceDir)

    def saveState(self):
        """Save current state of GUI to configuration file"""

        mySettings = QSettings()

        mySettings.setValue('inasafe/lastSourceDir', self.sourceDir)
        mySettings.setValue('inasafe/baseDataDir', self.baseDataDir)
        mySettings.setValue('inasafe/reportDir', self.reportDir)
        mySettings.setValue('inasafe/ignoreBaseDataDir', self.ignoreBaseDataDir)

    def showOptionDialog(self):
        """ Show option dialog """

        myOptions = BatchOption.getOptions(
            self.baseDataDir, self.reportDir, self.ignoreBaseDataDir)

        if myOptions:
            (self.baseDataDir, self.reportDir, self.ignoreBaseDataDir) = \
                myOptions

            self.saveState()

    def runScriptTask(self, theFilename):
        """ Run a python script in QGIS to exercise InaSAFE functionality.

        This functionality was originally intended for verifying that the key
        elements are InaSAFE are loading correctly and available. However,
        the utility of this function is such that you can run any arbitrary
        python scripts with it. As such you can use it it automate
        activities in QGIS, for example automatically running an impact
        assessment in response to an event.

        Args:
           * theFilename: str - the script filename.
           * theCount: int - the number of times the script must be run.

        Returns:
           not applicable

        Raises:
           no exceptions explicitly raised
        """

        # import script module
        myModule, _ = os.path.splitext(theFilename)
        if myModule in sys.modules:
            myScript = reload(sys.modules[myModule])
        else:
            myScript = __import__(myModule)

        # run as a new project
        self.iface.newProject()

        # run entry function
        myFunction = myScript.runScript
        if myFunction.func_code.co_argcount == 1:
            myFunction(self.iface)
        else:
            myFunction()

    def runScenarioTask(self, theIndex, theTask):
        """Run a simple scenario.
        After scenario has finished, pdf report will be created in
        directory self.reportDir

        Params:
            theItem - a dictionary contains the scenario configuration
        Returns:
            True if success, otherwise return False.
        """

        # always run in new project
        self.iface.newProject()

        myMessage = 'Loading layers: \nRoot: %s\n%s' % (theTask['path'], theTask['layers'])
        LOGGER.info(myMessage)

        try:
            macro.addLayers(theTask['path'], theTask['layers'])
        except QgisPathError:
            # set status to 'fail'
            LOGGER.exception('Loading layers failed: \nRoot: %s\n%s' % (
                theTask['path'], theTask['layers']))
            return False

        # See if we have a preferred impact function
        if 'function' in theTask:
            myFunctionId = theTask['function']
            myResult = macro.setFunctionId(myFunctionId)
            if not myResult:
                LOGGER.error("cannot set function %s" % theTask['function'])
                return False

        # set aggregation layer if exist
        myAggregation = theTask.get('aggregation')
        if myAggregation:
            myResult = macro.setAggregation(myAggregation)
            if not myResult:
                LOGGER.error("cannot set aggregation %s" % myAggregation)
                return False

        # set extent if exist
        if 'extent' in theTask:
            # split extent string
            myCoordinate = theTask['extent'].replace(' ', '').split(',')
            myCount = len(myCoordinate)
            if myCount != 4:
                myMessage = 'extent need exactly 4 value but got %s instead' % myCount
                LOGGER.error(myMessage)
                return False

            # parse the value to float type
            try:
                myCoordinate = [float(i) for i in myCoordinate]
            except ValueError as e:
                myMessage = e.message
                LOGGER.error(myMessage)
                return False

            # set the extent according the value
            myExtent = QgsRectangle(*myCoordinate)

            myMessage = 'set layer extent to %s ' % myExtent.asWktCoordinates()
            LOGGER.info(myMessage)

            self.iface.mapCanvas().setExtent(myExtent)

        def onAnalysisDone():

            # set status to success
            self.model.setStatus(theIndex, TaskModel.Success)

            # NOTE(gigih):
            # Usually after analysis is done, the impact layer
            # become the active layer. <--- WRONG
            myImpactLayer = self.iface.activeLayer()
            myReportDir = str(self.reportDir)
            self.createPDFReport(theTask['label'], myReportDir, myImpactLayer)

        LOGGER.info("Run scenario %s" % theTask['label'])
        macro.runScenario(onAnalysisDone)

        return True

    # @pyqtSignature('')
    # def on_pbnRunAll_clicked(self):
    #     myReport = []
    #     myFailCount = 0
    #     myPassCount = 0
    #
    #     for myRow in range(self.tblScript.rowCount()):
    #         myItem = self.tblScript.item(myRow, 0)
    #         myStatusItem = self.tblScript.item(myRow, 1)
    #
    #         try:
    #             myResult = self.runTask(myItem, myStatusItem)
    #             if myResult:
    #                 # P for passed
    #                 myReport.append('P: %s\n' % str(myItem))
    #                 myPassCount += 1
    #             else:
    #                 myReport.append('F: %s\n' % str(myItem))
    #                 myFailCount += 1
    #         except:
    #             LOGGER.exception('Batch execution failed')
    #             myReport.append('F: %s\n' % str(myItem))
    #             myFailCount += 1
    #
    #     self.showBatchReport(myReport, myPassCount, myFailCount)

    def showBatchReport(self, myReport, myPassCount, myFailCount):
        """Display a report status of Batch Runner"""

        myPath = os.path.join(temp_dir(), 'batch-report.txt')
        myReportFile = file(myPath, 'wt')
        myReportFile.write(' InaSAFE Batch Report File\n')
        myReportFile.write('-----------------------------\n')
        for myLine in myReport:
            myReportFile.write(myLine)
        myReportFile.write('-----------------------------\n')
        myReportFile.write('Total passed: %s\n' % myPassCount)
        myReportFile.write('Total failed: %s\n' % myFailCount)
        myReportFile.write('Total tasks: %s\n' % len(myReport))
        myReportFile.write('-----------------------------\n')
        myReportFile.close()
        LOGGER.info('Log written to %s' % myPath)
        myUrl = QtCore.QUrl('file:///' + myPath)
        QtGui.QDesktopServices.openUrl(myUrl)

    def runAllTask(self):
        """ Run all batch runner tasks """

        # check existing pdf report and
        # ask user if he want to rewrite existing report
        myPath = str(self.reportDir)
        myTitles = [myTask['label'] for myTask in self.model.tasks]

        myResult = self.checkExistingPDFReport(myPath, myTitles)
        if myResult is False:
            return False

        # run all task without checking existing report
        for myIndex in range(0, len(self.model.tasks)):
            self.runTask(myIndex, False)

    def runTask(self, theIndex, theCheckExistingReport=True):
        myTask = self.model.tasks[theIndex]

        # set status to running...
        self.model.setStatus(theIndex, TaskModel.Running)

        if myTask['type'] == 'script':
            self.runScriptTask(myTask['source'])
        elif myTask['type'] == 'scenario':
            myPath = str(self.reportDir)
            myTitle = myTask['label']

            if theCheckExistingReport:
                # check existing pdf report
                myResult = self.checkExistingPDFReport(myPath, [myTitle])
                if myResult is False:
                    self.model.setStatus(theIndex. TaskModel.Normal)
                    return False

            self.runScenarioTask(theIndex, myTask)
            #
            # # NOTE(gigih):
            # # Usually after analysis is done, the impact layer
            # # become the active layer. <--- WRONG
            # myImpactLayer = self.iface.activeLayer()
            # self.createPDFReport(myTitle, myPath, myImpactLayer)
        else:
            LOGGER.exception('task type not supported: "%s"' % myTask['type'])
            myResult = False

    # def runTask(self, theItem, theStatusItem, theCount=1):
    #     """Run a single task """
    #
    #     # set status to 'running'
    #     theStatusItem.setText(self.tr('Running'))
    #
    #     # .. seealso:: :func:`appendRow` to understand the next 2 lines
    #     myVariant = theItem.data(QtCore.Qt.UserRole)
    #     myValue = myVariant.toPyObject()[0]
    #
    #     myResult = True
    #
    #     if isinstance(myValue, str):
    #         myFilename = myValue
    #         # run script
    #         try:
    #             self.runScriptTask(myFilename, theCount)
    #             # set status to 'OK'
    #             theStatusItem.setText(self.tr('OK'))
    #         except Exception as ex:
    #             # set status to 'fail'
    #             theStatusItem.setText(self.tr('Fail'))
    #
    #             LOGGER.exception('Running macro failed')
    #             myResult = False
    #     elif isinstance(myValue, dict):
    #         myPath = str(self.leBaseDataDir.text())
    #         myTitle = str(theItem.text())
    #
    #         # check existing pdf report
    #         myResult = self.checkExistingPDFReport(myPath, [myTitle])
    #         if myResult is False:
    #             return False
    #
    #         # Its a dict containing files for a scenario
    #         myResult = self.runScenarioTask(myValue)
    #         if not myResult:
    #             theStatusItem.setText(self.tr('Fail'))
    #             myResult = False
    #         else:
    #
    #             # NOTE(gigih):
    #             # Usually after analysis is done, the impact layer
    #             # become the active layer. <--- WRONG
    #             myImpactLayer = self.iface.activeLayer()
    #             self.createPDFReport(myTitle, myPath, myImpactLayer)
    #     else:
    #         LOGGER.exception('data type not supported: "%s"' % myValue)
    #         myResult = False
    #
    #     return myResult

    def getPDFReportPath(self, theBasePath, theTitle):
        """Get PDF report filename based on theBasePath and theTitle.
        Params:
            * theBasePath - base path of pdf report file
            * theTitle - title of report
        Returns:
            a tuple contains the pdf report filename like this
            ('/home/foo/data/title.pdf', '/home/foo/data/title_table.pdf')
        """

        myFileName = theTitle.replace(' ', '_')
        myFileName = myFileName + '.pdf'
        myMapPath = os.path.join(theBasePath, myFileName)
        myTablePath = os.path.splitext(myMapPath)[0] + '_table.pdf'

        return (myMapPath, myTablePath)

    def checkExistingPDFReport(self, theBasePath, theTitles):
        """ Check the existence of pdf report in theBasePath.

        Params:
            * theBasePath - base path of pdf report file
            * theTitle - list of report titles
        Returns:
            True if theBasePath contains no reports or User
            agree to overwrite the report, otherwise return False.
        """

        myPaths = []
        for theTitle in theTitles:
            myPDFPaths = self.getPDFReportPath(theBasePath, theTitle)
            myPDFPaths = [x for x in myPDFPaths if os.path.exists(x)]
            myPaths.extend(myPDFPaths)

        # if reports are not founds, just return True
        if len(myPaths) == 0:
            return True

        # setup message box widget
        myMessage = self.tr(
            "PDF Report already exist in %1. Rewrite the files?")
        myMessage = myMessage.arg(theBasePath)

        myDetail = 'Existing PDF Report: \n'
        myDetail = myDetail + '\n'.join(myPaths)

        myMsgBox = QMessageBox(self)
        myMsgBox.setIcon(QMessageBox.Question)
        myMsgBox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        myMsgBox.setText(myMessage)
        myMsgBox.setDetailedText(myDetail)

        # return the result
        myResult = myMsgBox.exec_()
        return myResult == QMessageBox.Yes

    def createPDFReport(self, theTitle, theBasePath, theImpactLayer):
        """Create PDF report from impact layer.
        Create map & table report PDF based from theImpactLayer data.

        Params:
            * theTitle : the report title.
                         Output filename is based from this variable.
            * theBasePath : output directory
            * theImpactLayer : impact layer instance.

        See also:
            Dock.printMap()
        """

        myMap = Map(self.iface)

        # FIXME: check if theImpactLayer is the real impact layer...
        myMap.setImpactLayer(theImpactLayer)

        LOGGER.debug('Create Report: %s' % theTitle)
        myMapPath, myTablePath = self.getPDFReportPath(theBasePath, theTitle)

        # create map pdf
        myMap.printToPdf(myMapPath)

        # create table report pdf
        myHtmlRenderer = HtmlRenderer(myMap.pageDpi)
        myKeywords = myMap.keywordIO.readKeywords(theImpactLayer)
        myHtmlRenderer.printImpactTable(myKeywords, myTablePath)

        LOGGER.debug("report done %s %s" % (myMapPath, myTablePath))

    def saveCurrentScenario(self):
        """ Save current scenario to text file """
        myTitleDialog = self.tr('Save Scenario')
        myFileName = QFileDialog.getSaveFileName(
            self, myTitleDialog,
            self.sourceDir,
            "Text files (*.txt)"
        )

        # user press 'cancel'
        if not myFileName:
            return

        ### get data layer
        myDock = macro.getDock()

        # get absolute path of exposure & hazard layer
        myExposureLayer = myDock.getExposureLayer()
        myHazardLayer = myDock.getHazardLayer()

        myExposurePath = myExposureLayer.publicSource()
        myHazardPath = myHazardLayer.publicSource()
        myRootPath = os.path.commonprefix([myExposurePath, myHazardPath])

        # get title from keyword, otherwise use filename as title
        try:
            myTitle = myDock.keywordIO.readKeywords(myHazardLayer, 'title')
        except KeywordNotFoundError:
            myTitle = os.path.basename(str(myHazardPath))
            myTitle = os.path.splitext(myTitle)[0]

        myTitle = safeTr(myTitle)
        myFunctionId = myDock.getFunctionID(myDock.cboFunction.currentIndex())

        # simplify the path
        myExposurePath = myExposurePath.split(myRootPath)[1]
        myHazardPath = myHazardPath.split(myRootPath)[1]

        # write to file
        myParser = ConfigParser()
        myParser.add_section(myTitle)
        myParser.set(myTitle, 'path', myRootPath)
        myParser.set(myTitle, 'exposure', myExposurePath)
        myParser.set(myTitle, 'hazard', myHazardPath)
        myParser.set(myTitle, 'function', myFunctionId)

        myParser.write(open(myFileName, 'w'))
        LOGGER.info("save current scenario to %s" % myFileName)


def readScenarios(theFileName):
    """Read keywords dictionary from file

    Args:
        theFilename: Name of file holding scenarios .

    Returns:
        Dictionary of with structure like this
        {{ 'foo' : { 'a': 'b', 'c': 'd'},
            { 'bar' : { 'd': 'e', 'f': 'g'}}

    Raises: None

    A scenarios file may look like this:

        [jakarta_flood]
        hazard: /path/to/hazard.tif
        exposure: /path/to/exposure.tif
        function: function_id
        aggregation: /path/to/aggregation_layer.tif
        extent: minx, miny, maxx, maxy
    """

    # Input checks
    myFilename = os.path.abspath(theFileName)

    myBlocks = {}
    myParser = ConfigParser()

    # Parse the file content.
    # if the content don't have section header
    # we use the filename.
    try:
        myParser.read(myFilename)
    except MissingSectionHeaderError:
        myBaseName = os.path.basename(theFileName)
        myName = os.path.splitext(myBaseName)[0]
        mySection = '[%s]\n' % myName
        myContent = mySection + open(myFilename).read()
        myParser.readfp(StringIO(myContent))

    # convert to dictionary
    for mySection in myParser.sections():
        myItems = myParser.items(mySection)
        myBlocks[mySection] = {}
        for myKey, myValue in myItems:
            myBlocks[mySection][myKey] = myValue

    # Ok we have generated a structure that looks like this:
    # myBlocks = {{ 'foo' : { 'a': 'b', 'c': 'd'},
    #           { 'bar' : { 'd': 'e', 'f': 'g'}}
    # where foo and bar are scenarios and their dicts are the options for
    # that scenario (e.g. hazard, exposure etc)
    return myBlocks


def appendRow(theTable, theLabel, theData):
    """ Append new row to table widget.
     Args:
        * theTable - a QTable instance
        * theLabel - label for the row.
        * theData  - custom data associated with theLabel value.
     Returns:
        None
     Raises:
        None
    """
    myRow = theTable.rowCount()
    theTable.insertRow(theTable.rowCount())

    myItem = QTableWidgetItem(theLabel)

    # see for details of why we follow this pattern
    # http://stackoverflow.com/questions/9257422/
    # how-to-get-the-original-python-data-from-qvariant
    # Make the value immutable.
    myVariant = QVariant((theData,))
    # To retrieve it again you would need to do:
    #myValue = myVariant.toPyObject()[0]
    myItem.setData(Qt.UserRole, myVariant)

    theTable.setItem(myRow, 0, myItem)
    theTable.setItem(myRow, 1, QTableWidgetItem(''))




### Experiment ---------------------------------------------------
# from PyQt4.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, QEvent
# from PyQt4.QtGui import QItemDelegate, QStyledItemDelegate, QPen, QStyle, QBrush, QStyleOptionButton, QPainter
from PyQt4.QtCore import *
from PyQt4.QtGui import *


###
class TaskModel(QAbstractListModel):
    """Task Model is class that contains all batch runner tasks"""

    # Enum for status of task
    Normal = 0
    Running = 1
    Fail = 3
    Success = 4

    def __init__(self, theParent=None):
        """ Initialize TaskModel.
        Params:
            * theParent - parent widget
        """

        QAbstractListModel.__init__(self, theParent)

        self.tasks = []

    def rowCount(self, theParent=QModelIndex()):
        """ Get the task count """
        return len(self.tasks)

    def data(self, theIndex, theRole):
        myData = self.tasks[theIndex.row()]

        if theIndex.isValid() and theRole == Qt.DisplayRole:
            return QVariant(myData['label'])
        else:
            # see for details of why we follow this pattern
            # http://stackoverflow.com/questions/9257422/
            # how-to-get-the-original-python-data-from-qvariant
            # Make the value immutable.
            myVariant = QVariant((myData,))

            return myVariant

    def setStatus(self, theIndex, theFlag):
        """ Set status of task
        Params:
            * theIndex - index of task in Model
            * theFlag - status value of task:
                        TaskModel.Normal, TaskModel.Running,
                        TaksModel.Fail, or TaskModel.Success
        """
        self.tasks[theIndex]['status'] = theFlag
        myModelIndex = self.index(theIndex, 0)
        self.dataChanged.emit(myModelIndex, myModelIndex)

    def populate(self, theSourcePath, theDataPath):
        """ Populate model with files from theSourcePath directory.

        Args:
            theSourcePath : QString - path where .txt & .py reside
            theDataPath : QString - default data layer directory for scenario file to search
        """

        self.tasks = []

        myPath = str(theSourcePath)

        # only support .py and .txt files
        for myFile in os.listdir(myPath):
            myExt = os.path.splitext(myFile)[1]
            myAbsPath = os.path.join(myPath, myFile)

            if myExt == '.py':
                #appendRow(self.tblScript, myFile, myAbsPath)
                self.tasks.append({
                    'type': 'script',
                    'label': myFile,
                    'source': os.path.join(myAbsPath, myFile)
                })

            elif myExt == '.txt':
                # insert scenarios from file into table widget
                for myKey, myValue in readScenarios(myAbsPath).iteritems():

                    myLayers = []
                    ## NOTE: hazard & exposure is must!!
                    if 'hazard' in myValue:
                        myLayers.append(myValue['hazard'])
                    if 'exposure' in myValue:
                        myLayers.append(myValue['exposure'])

                    # optional
                    if 'aggregation' in myValue:
                        myLayers.append(myValue['aggregation'])

                    self.tasks.append({
                        'type': 'scenario',
                        'label': myKey,
                        'path': myValue.get('path') or theDataPath,
                        'hazard': myValue['hazard'],
                        'exposure': myValue['exposure'],
                        'function': myValue['function'],
                        'aggregation': myValue.get('aggregation'),
                        'layers': myLayers
                    })


class TaskItemDelegate(QStyledItemDelegate):
    runClicked = pyqtSignal(int)

    def __init__(self, theParent=None, *theArgs):
        QStyledItemDelegate.__init__(self, theParent, *theArgs)

        self.runButtonStyle = QStyleOptionButton()
        #self.runButtonStyle.rect = myButtonRect
        self.runButtonStyle.text = self.tr("Run")
        self.runButtonStyle.state = QStyle.State_Enabled

    def getRunButtonRect(self, theOption):
        myRect = QRect(theOption.rect)
        myRect.setX(theOption.rect.width() - 40)
        myRect.setWidth(40)
        #myRect.setY(theOption.rect.y() - 10)
        myRect.setHeight(30)

        return myRect

    def getProgressBarRect(self, theOption):
        myRect = QRect(theOption.rect)
        myRect.setX(theOption.rect.width() - 40)
        myRect.setWidth(240)
        myRect.setY(theOption.rect.y() - 20)
        myRect.setHeight(18)

        return myRect

    def sizeHint(self, theOption, theIndex=None):
        return QSize(180, 90)

    def paint(self, thePainter, theOption, theIndex):

        myAppStyle = QApplication.style()
        thePainter.save()

        thePainter.setRenderHint(QPainter.Antialiasing, True)

        # draw icon

        # draw text
        thePainter.setPen(QPen(Qt.black))
        value = theIndex.data(Qt.DisplayRole)
        if value.isValid():
            text = value.toString()
            thePainter.drawText(theOption.rect, Qt.AlignLeft, text)

        # draw run button
        # myButtonRect = QRect(option.rect)
        #
        # myButton = QStyleOptionButton()
        # myButton.rect = myButtonRect
        # myButton.text = self.tr("Run")
        # myButton.state = QStyle.State_Enabled
        myButton = self.runButtonStyle
        myButton.rect = self.getRunButtonRect(theOption)
        myAppStyle.drawControl(QStyle.CE_PushButton, myButton, thePainter)

        # draw progress bar

        # .. seealso:: :func:`appendRow` to understand the next 2 lines
        myVariant = theIndex.data(Qt.UserRole)
        myTask = myVariant.toPyObject()[0]
        myStatus = myTask.get('status')

        if myStatus == TaskModel.Running:
            myProgressBar = QStyleOptionProgressBar()
            myProgressBar.rect = self.getProgressBarRect(theOption)
            myAppStyle.drawControl(QStyle.CE_ProgressBar, myProgressBar, thePainter)
        elif myStatus == TaskModel.Fail:
            LOGGER.info("Fail bro")
        elif myStatus == TaskModel.Success:
            LOGGER.info("Sukses")

        thePainter.restore()

    def editorEvent(self, theEvent, theItemModel, theOption, theModelIndex):
        """
        :param theEvent:QEvent
        :param theItemModel:
        :param theStyle:
        :param theModelIndex:
        :return:
        """

        ##

        if theEvent.type() == QEvent.MouseButtonRelease:
            myRunButtonRect = self.getRunButtonRect(theOption)
            if myRunButtonRect.contains(theEvent.pos()):
                self.runClicked.emit(theModelIndex.row())



        # myButtonRect = QRect(option.rect)
        #
        # myButtonRect.setX(option.rect.width() - 40)
        # myButtonRect.setWidth(40)
        # #myButtonRect.setY(option.rect.y() - 10)
        # myButtonRect.setHeight(30)
        return False

if __name__ == '__main__':
    from PyQt4.QtGui import QApplication
    from PyQt4.QtCore import QCoreApplication
    import sys

    QCoreApplication.setOrganizationDomain('aifdr')
    QCoreApplication.setApplicationName('inasafe')

    app = QApplication(sys.argv)
    a = BatchRunner()


    ### create content
    # list_data = [1, 2, 3, 4]
    # lm = MyListModel(list_data, a)
    # de = MyDelegate()
    #
    # a.lvTask.setModel(lm)
    # a.lvTask.setItemDelegate(de)

    a.show()

    #a.saveCurrentScenario()

    app.exec_()
