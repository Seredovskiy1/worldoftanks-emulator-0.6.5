# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/common/ArenaType.py
# Compiled at: 2011-05-26 15:49:22
import ResMgr
from debug_utils import *
from constants import IS_CLIENT
from constants import IS_BASEAPP
from constants import ARENA_TYPE_XML_PATH
COMMON_XML = ARENA_TYPE_XML_PATH + '_common_.xml'
if IS_CLIENT:
    from helpers import i18n
g_list = None
g_cache = None

def init():
    global g_cache
    global g_list
    g_cache = Cache()
    g_list = {}
    rootSection = ResMgr.openSection(ARENA_TYPE_XML_PATH + '_list_.xml')
    if rootSection is None:
        raise Exception, "Can't open '%s'" % ARENA_TYPE_XML_PATH + '_list_.xml'
    for (key, value) in rootSection.items():
        g_list[value.readInt('id')] = value.readString('name')

    return


def reload():
    from sys import modules
    import __builtin__
    __builtin__.reload(modules[reload.__module__])
    init()
    return


class ArenaType(object):

    def __init__(self, typeID, typeName, section):
        self.typeID = typeID
        self.typeName = typeName
        commonXml = ResMgr.openSection(COMMON_XML)
        if commonXml is None:
            raise NameError, 'can not open ' + COMMON_XML
        self.geometry = self.__readString('geometry', section, commonXml)
        self.minPlayersInTeam = self.__readInt('minPlayersInTeam', section, commonXml)
        if self.minPlayersInTeam < 0:
            self.__raiseWrongXml("wrong 'minPlayersInTeam' value")
        self.maxPlayersInTeam = self.__readInt('maxPlayersInTeam', section, commonXml)
        if self.maxPlayersInTeam < 0:
            self.__raiseWrongXml("wrong 'maxPlayersInTeam' value")
        if self.maxPlayersInTeam < self.minPlayersInTeam:
            self.__raiseWrongXml("'maxPlayersInTeam' value < 'minPlayersInTeam' value")
        self.roundLength = self.__readInt('roundLength', section, commonXml)
        if self.roundLength < 0:
            self.__raiseWrongXml("wrong 'roundLength' value")
        bottomLeft = section.readVector2('boundingBox/bottomLeft')
        upperRight = section.readVector2('boundingBox/upperRight')
        if bottomLeft[0] >= upperRight[0] or bottomLeft[1] >= upperRight[1]:
            self.__raiseWrongXml("wrong 'boundingBox' values")
        self.boundingBox = (
         bottomLeft, upperRight)
        self.weatherPresets = self.__readWeatherPresets(section)
        if IS_CLIENT:
            self.name = i18n.makeString(self.__readString('name', section, commonXml))
            self.description = i18n.makeString(self.__readString('description', section, commonXml))
            self.minimapConfig = self.__readMinimapConfig(section)
            self.music = self.__readString('music', section, commonXml)
            self.loadingMusic = self.__readString('loadingMusic', section, commonXml)
            self.ambientSound = self.__readString('ambientSound', section, commonXml)
            self.umbraEnabled = self.__readInt('umbraEnabled', section, commonXml)
            self.batchingEnabled = self.__readInt('batchingEnabled', section, commonXml)
            self.waterTexScale = section.readFloat('water/texScale', 0.5)
            self.waterFreqX = section.readFloat('water/freqX', 1.0)
            self.waterFreqZ = section.readFloat('water/freqZ', 1.0)
            self.defaultGroundEffect = None
            defaultGroundEff = section.readString('defaultGroundEffect').strip()
            if defaultGroundEff == '':
                defaultGroundEff = commonXml.readString('defaultGroundEffect').strip()
            if defaultGroundEff != '':
                if defaultGroundEff.find('|') != -1:
                    defaultGroundEff = defaultGroundEff.split('|')
                    for i in xrange(0, len(defaultGroundEff)):
                        defaultGroundEff[i] = defaultGroundEff[i].strip()

                self.defaultGroundEffect = defaultGroundEff
        if IS_BASEAPP:
            self.kickAfterFinishWaitTime = self.__readFloat('kickAfterFinishWaitTime', section, commonXml)
            if self.kickAfterFinishWaitTime < 0:
                self.__raiseWrongXml("wrong 'kickAfterFinishWaitTime' value")
            self.arenaStartDelay = self.__readFloat('arenaStartDelay', section, commonXml)
            if self.arenaStartDelay <= 0:
                self.__raiseWrongXml("wrong 'arenaStartDelay' value")
        return

    def __readString(self, key, xml, commonXml):
        value = xml.readString(key)
        if value == '':
            value = commonXml.readString(key)
            if value == '':
                self.__raiseWrongXml("missing key '%s'" % key)
        return value

    def __readFloat(self, key, xml, commonXml):
        value = xml.readFloat(key, -1.0)
        if value == -1.0:
            value = commonXml.readFloat(key, -1.0)
            if value == -1.0:
                self.__raiseWrongXml("missing key '%s'" % key)
        return value

    def __readInt(self, key, xml, commonXml):
        value = xml.readInt(key, -1)
        if value == -1:
            value = commonXml.readInt(key, -1)
            if value == -1:
                self.__raiseWrongXml("missing key '%s'" % key)
        return value

    def __readMinimapConfig(self, section):
        minimapXML = section['minimapConfig']
        if minimapXML is None:
            self.__raiseWrongXml("missing key 'minimapConfig'")
        cfg = {}
        spaceMap = minimapXML.readString('spaceMap')
        if spaceMap == '':
            self.__raiseWrongXml("missing key 'minimapConfig/spaceMap'")
        cfg['spaceMap'] = spaceMap
        return cfg

    def __readWeatherPresets(self, section):
        weatherXML = section['weather']
        if weatherXML is None or not weatherXML:
            return [{'rnd_range': (0, 1)}]
        else:
            presets = []
            possibilitySum = 0
            for presetXML in weatherXML.values():
                preset = {}
                for (key, valueXML) in presetXML.items():
                    preset[key] = valueXML.asString

                presets.append(preset)
                possibilitySum += presetXML.readFloat('possibility', 1.0)

            factor = 1 / possibilitySum
            prev_upper_limit = 0
            for preset in presets:
                possibility = float(preset.pop('possibility', 1.0))
                rnd_range = (prev_upper_limit, prev_upper_limit + possibility * factor)
                preset['rnd_range'] = rnd_range
                prev_upper_limit = rnd_range[1]

            return presets

    def __raiseWrongXml(self, msg):
        raise Exception, "wrong arena type XML '%s': %s" % (self.typeID, msg)
        return


class Cache(object):

    def __init__(self):
        self.__cont = {}
        return

    def get(self, typeID):
        ct = self.__cont.get(typeID)
        if ct:
            return ct
        else:
            typeName = g_list.get(typeID, None)
            if typeName is None:
                raise NameError, 'can not get arena type name (%d)' % typeID
            sectionName = ARENA_TYPE_XML_PATH + typeName + '.xml'
            section = ResMgr.openSection(sectionName)
            if section is None:
                raise NameError, "can not open '%s'" % sectionName
            ct = ArenaType(typeID, typeName, section)
            self.__cont[typeID] = ct
            section = None
            ResMgr.purge(sectionName, True)
            return ct

    def clear(self):
        self.__cont.clear()
        return


return

# okay decompiling C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\common\ArenaType.pyc
