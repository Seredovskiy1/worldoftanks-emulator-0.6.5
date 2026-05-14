# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/common/dossiers/__init__.py
# Compiled at: 2011-05-26 15:49:22
import collections, time, nations, constants
from items import vehicles
from functools import partial
from itertools import izip, chain
from dossiers.config import RECORD_CONFIGS
from constants import DOSSIER_TYPE
from struct import *
from array import array
from debug_utils import *
ACCOUNT_DOSSIER_VERSION = 19
VEHICLE_DOSSIER_VERSION = 17
TANKMAN_DOSSIER_VERSION = 10
RECORD_NAMES = ('reserved', 'xp', 'maxXP', 'battlesCount', 'wins', 'losses', 'survivedBattles',
                'lastBattleTime', 'battleLifeTime', 'winAndSurvived', 'battleHeroes',
                'frags', 'maxFrags', 'frags8p', 'fragsBeast', 'shots', 'hits', 'spotted',
                'damageDealt', 'damageReceived', 'treesCut', 'capturePoints', 'droppedCapturePoints',
                'sniperSeries', 'maxSniperSeries', 'invincibleSeries', 'maxInvincibleSeries',
                'diehardSeries', 'maxDiehardSeries', 'killingSeries', 'maxKillingSeries',
                'piercingSeries', 'maxPiercingSeries', 'vehTypeFrags', 'warrior',
                'invader', 'sniper', 'defender', 'steelwall', 'supporter', 'scout',
                'medalKay', 'medalCarius', 'medalKnispel', 'medalPoppel', 'medalAbrams',
                'medalLeClerc', 'medalLavrinenko', 'medalEkins', 'medalWittmann',
                'medalOrlik', 'medalOskin', 'medalHalonen', 'medalBurda', 'medalBillotte',
                'medalKolobanov', 'medalFadin', 'tankExpert', 'titleSniper', 'invincible',
                'diehard', 'raider', 'handOfDeath', 'armorPiercer', 'kamikaze', 'lumberjack',
                'beasthunter', 'mousebane', 'creationTime', 'maxXPVehicle', 'maxFragsVehicle',
                'vehDossiersCut')
RECORD_INDICES = dict((x[1], x[0]) for x in enumerate(RECORD_NAMES))
RECORD_DEFAULT_VALUES = {'vehTypeFrags': {}, 'vehDossiersCut': {}}
POP_UP_RECORDS = set([46, 47, 48, 49, 50, 51, 52, 
 53, 54, 55, 56, 57, 58, 
 59, 60, 61, 
 62, 63, 64, 
 65, 66, 67, 68, 78, 79, 
 69, 70, 71, 72, 73, 74, 
 75, 
 76, 77])
EVENT_RECORDS = set([70, 71, 72, 74, 75])

def _set_RECORD_PACKING():
    global _RECORD_PACKING
    _RECORD_PACKING = {'_version': ('p', 'H', 2, 32767), 
       'warrior': ('p', 'H', 2, 60001), 
       'invader': ('p', 'H', 2, 60001), 
       'sniper': ('p', 'H', 2, 60001), 
       'defender': ('p', 'H', 2, 60001), 
       'steelwall': ('p', 'H', 2, 60001), 
       'supporter': ('p', 'H', 2, 60001), 
       'scout': ('p', 'H', 2, 60001), 
       'battleHeroes': ('p', 'H', 2, 60001), 
       'treesCut': ('p', 'H', 2, 60001), 
       'maxXP': ('p', 'H', 2, 60001), 
       'sniperSeries': ('p', 'H', 2, 60001), 
       'maxSniperSeries': ('p', 'H', 2, 60001), 
       'invincibleSeries': ('p', 'B', 1, 201), 
       'maxInvincibleSeries': ('p', 'B', 1, 201), 
       'diehardSeries': ('p', 'B', 1, 201), 
       'maxDiehardSeries': ('p', 'B', 1, 201), 
       'killingSeries': ('p', 'B', 1, 201), 
       'maxKillingSeries': ('p', 'B', 1, 201), 
       'piercingSeries': ('p', 'B', 1, 201), 
       'maxPiercingSeries': ('p', 'B', 1, 201), 
       'maxFrags': ('p', 'B', 1, 201), 
       'xp': ('p', 'I', 4, 4000000001L), 
       'battlesCount': ('p', 'I', 4, 4000000001L), 
       'wins': ('p', 'I', 4, 4000000001L), 
       'losses': ('p', 'I', 4, 4000000001L), 
       'survivedBattles': ('p', 'I', 4, 4000000001L), 
       'winAndSurvived': ('p', 'I', 4, 4000000001L), 
       'lastBattleTime': ('p', 'I', 4, 4000000001L), 
       'frags': ('p', 'I', 4, 4000000001L), 
       'frags8p': ('p', 'I', 4, 4000000001L), 
       'fragsBeast': ('p', 'I', 4, 4000000001L), 
       'shots': ('p', 'I', 4, 4000000001L), 
       'hits': ('p', 'I', 4, 4000000001L), 
       'spotted': ('p', 'I', 4, 4000000001L), 
       'damageDealt': ('p', 'I', 4, 4000000001L), 
       'damageReceived': ('p', 'I', 4, 4000000001L), 
       'capturePoints': ('p', 'I', 4, 4000000001L), 
       'droppedCapturePoints': ('p', 'I', 4, 4000000001L), 
       'battleLifeTime': ('p', 'I', 4, 4000000001L), 
       'creationTime': ('p', 'I', 4, 4000000001L), 
       'maxXPVehicle': ('p', 'I', 4, 4294967295L), 
       'maxFragsVehicle': ('p', 'I', 4, 4294967295L), 
       'medalKay': ('p', 'B', 1, 4), 
       'medalCarius': ('p', 'B', 1, 4), 
       'medalKnispel': ('p', 'B', 1, 4), 
       'medalPoppel': ('p', 'B', 1, 4), 
       'medalAbrams': ('p', 'B', 1, 4), 
       'medalLeClerc': ('p', 'B', 1, 4), 
       'medalLavrinenko': ('p', 'B', 1, 4), 
       'medalEkins': ('p', 'B', 1, 4), 
       'medalWittmann': ('p', 'B', 1, 201), 
       'medalOrlik': ('p', 'B', 1, 201), 
       'medalOskin': ('p', 'B', 1, 201), 
       'medalHalonen': ('p', 'B', 1, 201), 
       'medalBurda': ('p', 'B', 1, 201), 
       'medalBillotte': ('p', 'B', 1, 201), 
       'medalKolobanov': ('p', 'B', 1, 201), 
       'medalFadin': ('p', 'B', 1, 201), 
       'beasthunter': ('p', 'B', 1, 1), 
       'mousebane': ('p', 'B', 1, 201), 
       'tankExpert': ('p', 'B', 1, 1), 
       'titleSniper': ('p', 'B', 1, 1), 
       'invincible': ('p', 'B', 1, 1), 
       'diehard': ('p', 'B', 1, 1), 
       'raider': ('p', 'B', 1, 201), 
       'handOfDeath': ('p', 'B', 1, 1), 
       'armorPiercer': ('p', 'B', 1, 1), 
       'kamikaze': ('p', 'B', 1, 201), 
       'lumberjack': ('p', 'B', 1, 1), 
       'vehTypeFrags': (
                      'd', _getVehTypeFragsFmtValues, _unpackVehTypeFrags), 
       'vehDossiersCut': (
                        'd', _getVehDossiersCutFmtValues, _unpackVehDossiersCut)}
    return


def _set_RECORD_DEPENDENCIES():
    global _RECORD_DEPENDENCIES
    _RECORD_DEPENDENCIES = {'battleHeroes': (
                      ('warrior', 'invader', 'sniper', 'defender', 'steelwall', 'supporter', 'scout'),
                      _updateBattleHeroes), 
       'medalKay': (
                  ('battleHeroes', ), _updateMedalKay), 
       'medalCarius': (
                     ('frags', ), _updateMedalCarius), 
       'medalKnispel': (
                      ('damageDealt', 'damageReceived'), _updateMedalKnispel), 
       'medalPoppel': (
                     ('spotted', ), _updateMedalPoppel), 
       'medalAbrams': (
                     ('winAndSurvived', ), _updateMedalAbrams), 
       'medalLeClerc': (
                      ('capturePoints', ), _updateMedalLeClerc), 
       'medalLavrinenko': (
                         ('droppedCapturePoints', ), _updateMedalLavrinenko), 
       'medalEkins': (
                    ('frags8p', ), _updateMedalEkins), 
       'beasthunter': (
                     ('fragsBeast', ), _updateBeasthunter), 
       'maxSniperSeries': (
                         ('sniperSeries', ), _updateMaxSniperSeries), 
       'titleSniper': (
                     ('maxSniperSeries', ), _updateTitleSniper), 
       'maxInvincibleSeries': (
                             ('invincibleSeries', ), _updateMaxInvincibleSeries), 
       'invincible': (
                    ('maxInvincibleSeries', ), _updateInvincible), 
       'maxDiehardSeries': (
                          ('diehardSeries', ), _updateMaxDiehardSeries), 
       'diehard': (
                 ('maxDiehardSeries', ), _updateDiehard), 
       'maxKillingSeries': (
                          ('killingSeries', ), _updateMaxKillingSeries), 
       'handOfDeath': (
                     ('maxKillingSeries', ), _updateHandOfDeath), 
       'maxPiercingSeries': (
                           ('piercingSeries', ), _updateMaxPiercingSeries), 
       'armorPiercer': (
                      ('maxPiercingSeries', ), _updateArmorPiercer), 
       'lumberjack': (
                    ('treesCut', ), _updateLumberjack)}
    return


def _set_ACCOUNT_RECORD_DEPENDENCIES():
    global _ACCOUNT_RECORD_DEPENDENCIES
    global _ACCOUNT_RECORD_DEPENDENCIES2
    _ACCOUNT_RECORD_DEPENDENCIES = dict(_RECORD_DEPENDENCIES)
    _ACCOUNT_RECORD_DEPENDENCIES.update({'mousebane': (
                   ('vehTypeFrags', ), _updateMousebane), 
       'tankExpert': (
                    ('vehTypeFrags', ), _updateTankExpert)})
    _ACCOUNT_RECORD_DEPENDENCIES2 = _buildDependencies2(_ACCOUNT_RECORD_DEPENDENCIES)
    return


def _set_VEHICLE_RECORD_DEPENDENCIES():
    global _VEHICLE_RECORD_DEPENDENCIES
    global _VEHICLE_RECORD_DEPENDENCIES2
    _VEHICLE_RECORD_DEPENDENCIES = dict(_RECORD_DEPENDENCIES)
    _VEHICLE_RECORD_DEPENDENCIES.update({'mousebane': (
                   ('vehTypeFrags', ), _updateMousebane), 
       'tankExpert': (
                    ('vehTypeFrags', ), _updateTankExpert)})
    _VEHICLE_RECORD_DEPENDENCIES2 = _buildDependencies2(_VEHICLE_RECORD_DEPENDENCIES)
    return


def _set_TANKMAN_RECORD_DEPENDENCIES():
    global _TANKMAN_RECORD_DEPENDENCIES
    global _TANKMAN_RECORD_DEPENDENCIES2
    _TANKMAN_RECORD_DEPENDENCIES = dict(_RECORD_DEPENDENCIES)
    _TANKMAN_RECORD_DEPENDENCIES.update({})
    _TANKMAN_RECORD_DEPENDENCIES2 = _buildDependencies2(_TANKMAN_RECORD_DEPENDENCIES)
    return


def _set_ACCOUNT_RECORDS_LAYOUT():
    global _ACCOUNT_RECORDS_LAYOUT
    _ACCOUNT_RECORDS_LAYOUT = (
     [
      1, 2, 3, 4, 
      5, 6, 7, 
      8, 9, 10, 
      11, 12, 13, 14, 
      15, 16, 17, 18, 19, 20, 21, 
      22, 
      23, 24, 25, 26, 
      27, 28, 29, 30, 
      31, 
      32, 33, 34, 
      35, 36, 
      37, 38, 39, 40, 
      41, 42, 43, 
      44, 45, 46, 47, 48, 49, 
      50, 
      51, 52, 53, 54, 55, 
      56, 57, 58, 59, 
      60, 
      61, 62, 63, 64, 65, 66, 
      67, 68, 69, 70, 
      71],
     [
      'vehTypeFrags', 'vehDossiersCut'])
    _extendRecordPacking(_ACCOUNT_RECORDS_LAYOUT, '_dynRecPos_account')
    return


def _set_VEHICLE_RECORDS_LAYOUT():
    global _VEHICLE_RECORDS_LAYOUT
    _VEHICLE_RECORDS_LAYOUT = (
     [
      1, 
      2, 3, 4, 
      5, 6, 7, 8, 9, 10, 
      11, 
      12, 13, 14, 15, 16, 17, 18, 
      19, 
      20, 21, 22, 23, 
      24, 25, 26, 27, 
      28, 
      29, 30, 31, 
      32, 33, 
      34, 35, 36, 37, 
      38, 39, 40, 
      41, 42, 43, 44, 45, 46, 
      47, 
      48, 49, 50, 51, 52, 
      53, 54, 55, 56, 
      57, 
      58, 59, 60, 61, 62, 63, 
      64, 65, 66, 67, 
      68],
     [
      'vehTypeFrags'])
    _extendRecordPacking(_VEHICLE_RECORDS_LAYOUT, '_dynRecPos_vehicle')
    return


def _set_TANKMAN_RECORDS_LAYOUT():
    global _TANKMAN_RECORDS_LAYOUT
    _TANKMAN_RECORDS_LAYOUT = (
     [
      1, 
      2, 3, 4, 
      5, 6, 7, 8, 9, 10, 
      11, 
      12, 13, 14, 15, 16, 17, 18, 
      19, 
      20, 21, 22, 23, 
      24, 25, 26, 27, 
      28, 
      29, 30, 31, 
      32, 33, 
      34, 35, 36, 37, 
      38, 39, 40, 
      41, 42, 43, 44, 45, 46, 
      47, 
      48, 49, 50, 51, 52, 
      53, 54, 55, 56, 
      57, 
      58, 59, 60, 61, 
      62, 63, 64, 65, 
      66], [])
    _extendRecordPacking(_TANKMAN_RECORDS_LAYOUT, '_dynRecPos_tankman')
    return


def _set_STATIC_RECORD_POSITIONS():
    global _ACCOUNT_STATIC_RECORD_POSITIONS
    global _TANKMAN_STATIC_RECORD_POSITIONS
    global _VEHICLE_STATIC_RECORD_POSITIONS
    _ACCOUNT_STATIC_RECORD_POSITIONS = _buildStaticRecordPositions(_ACCOUNT_RECORDS_LAYOUT)
    _VEHICLE_STATIC_RECORD_POSITIONS = _buildStaticRecordPositions(_VEHICLE_RECORDS_LAYOUT)
    _TANKMAN_STATIC_RECORD_POSITIONS = _buildStaticRecordPositions(_TANKMAN_RECORDS_LAYOUT)
    return


def _set_STATIC_RECORDS_FMT():
    global _ACCOUNT_STATIC_RECORDS_FMT
    global _TANKMAN_STATIC_RECORDS_FMT
    global _VEHICLE_STATIC_RECORDS_FMT
    _ACCOUNT_STATIC_RECORDS_FMT = _buildStaticRecordsFmt(_ACCOUNT_RECORDS_LAYOUT)
    _VEHICLE_STATIC_RECORDS_FMT = _buildStaticRecordsFmt(_VEHICLE_RECORDS_LAYOUT)
    _TANKMAN_STATIC_RECORDS_FMT = _buildStaticRecordsFmt(_TANKMAN_RECORDS_LAYOUT)
    return


def getAccountDossierDescr(compDescr=''):
    return _DossierDescr(compDescr, _ACCOUNT_RECORDS_LAYOUT, '_dynRecPos_account', _ACCOUNT_STATIC_RECORD_POSITIONS, _ACCOUNT_STATIC_RECORDS_FMT, _ACCOUNT_RECORD_DEPENDENCIES2, ACCOUNT_DOSSIER_VERSION, _ACCOUNT_DOSSIER_UPDATERS)


def getVehicleDossierDescr(compDescr=''):
    return _DossierDescr(compDescr, _VEHICLE_RECORDS_LAYOUT, '_dynRecPos_vehicle', _VEHICLE_STATIC_RECORD_POSITIONS, _VEHICLE_STATIC_RECORDS_FMT, _VEHICLE_RECORD_DEPENDENCIES2, VEHICLE_DOSSIER_VERSION, _VEHICLE_DOSSIER_UPDATERS)


def getTankmanDossierDescr(compDescr=''):
    return _DossierDescr(compDescr, _TANKMAN_RECORDS_LAYOUT, '_dynRecPos_tankman', _TANKMAN_STATIC_RECORD_POSITIONS, _TANKMAN_STATIC_RECORDS_FMT, _TANKMAN_RECORD_DEPENDENCIES2, TANKMAN_DOSSIER_VERSION, _TANKMAN_DOSSIER_UPDATERS)


def getDossierDescr(dossierType, compDescr=''):
    if dossierType == DOSSIER_TYPE.VEHICLE:
        return getVehicleDossierDescr(compDescr)
    else:
        if dossierType == DOSSIER_TYPE.TANKMAN:
            return getTankmanDossierDescr(compDescr)
        if dossierType == DOSSIER_TYPE.ACCOUNT:
            return getAccountDossierDescr(compDescr)
        return


def getRecordMaxValue(record):
    recordPacking = _RECORD_PACKING[record]
    assert recordPacking[0] == 'p'
    return recordPacking[3]


def _buildDependencies2(dependencies):
    dependencies2 = collections.defaultdict(list)
    for (record, (affectingRecords, updater)) in dependencies.iteritems():
        for affectingRecord in affectingRecords:
            dependencies2[affectingRecord].append(updater)

    return dependencies2


def _extendRecordPacking(recordsLayout, recordName):
    dynRecordsCount = len(recordsLayout[1])
    _RECORD_PACKING[recordName] = ('s', '%dH' % dynRecordsCount, dynRecordsCount * 2,
     dynRecordsCount, _getTupleValues, _getTupleData)
    RECORD_DEFAULT_VALUES[recordName] = (0, ) * dynRecordsCount
    return


def _buildStaticRecordPositions(recordsLayout):
    positions = {}
    sum = 0
    for record in recordsLayout[0]:
        positions[record] = sum
        recordPacking = _RECORD_PACKING[record]
        assert recordPacking[0] == 'p' or recordPacking[0] == 's'
        sum += recordPacking[2]

    positions['_staticRecordsSize'] = sum
    return positions


def _buildStaticRecordsFmt(recordsLayout):
    fmt = '<'
    for record in recordsLayout[0]:
        fmt += _RECORD_PACKING[record][1]

    return fmt


def _getTupleValues(data):
    return data


def _getTupleData(values):
    return values


def _getVehTypeFragsFmtValues(vehTypeFrags):
    count = len(vehTypeFrags)
    fmt = 'H%dI%dH' % (count, count)
    values = [count]
    values += vehTypeFrags.keys()
    values += vehTypeFrags.values()
    return (fmt, values, 2 + 6 * count)


def _unpackVehTypeFrags(compDescr, offset):
    count = unpack('<H', compDescr[offset:offset + 2])[0]
    next_offset = offset + 2 + 6 * count
    values = unpack('<%dI%dH' % (count, count), compDescr[offset + 2:next_offset])
    data = {}
    for i in xrange(count):
        data[values[i]] = values[count + i]

    return (
     data, next_offset)


def _getVehDossiersCutFmtValues(vehDossiersCut):
    count = len(vehDossiersCut)
    fmt = 'H%dI' % (3 * count,)
    values = [count]
    for (vehTypeCompDescr, (battlesCount, wins)) in vehDossiersCut.iteritems():
        values += (vehTypeCompDescr, battlesCount, wins)

    return (
     fmt, values, 2 + 12 * count)


def _unpackVehDossiersCut(compDescr, offset):
    count = unpack('<H', compDescr[offset:offset + 2])[0]
    next_offset = offset + 2 + 12 * count
    values = unpack('<%dI' % (3 * count,), compDescr[offset + 2:next_offset])
    data = {}
    for i in xrange(count):
        data[values[3 * i]] = (
         values[3 * i + 1], values[3 * i + 2])

    return (
     data, next_offset)


def _updateBattleHeroes(dossierDescr, affectingRecord, value, prevValue):
    dossierDescr['battleHeroes'] = dossierDescr['battleHeroes'] + value - prevValue
    return


def _updateMedalKay(dossierDescr, affectingRecord, value, prevValue):
    medalKayCfg = RECORD_CONFIGS['medalKay']
    battleHeroes = dossierDescr['battleHeroes']
    maxMedalClass = len(medalKayCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if battleHeroes >= medalKayCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalKay']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalKay'] = medalClass
    return


def _updateMedalCarius(dossierDescr, affectingRecord, value, prevValue):
    medalCariusCfg = RECORD_CONFIGS['medalCarius']
    frags = dossierDescr['frags']
    maxMedalClass = len(medalCariusCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if frags >= medalCariusCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalCarius']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalCarius'] = medalClass
    return


def _updateMedalKnispel(dossierDescr, affectingRecord, value, prevValue):
    medalKnispelCfg = RECORD_CONFIGS['medalKnispel']
    damageDealt = dossierDescr['damageDealt']
    damageReceived = dossierDescr['damageReceived']
    maxMedalClass = len(medalKnispelCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if damageDealt + damageReceived >= medalKnispelCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalKnispel']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalKnispel'] = medalClass
    return


def _updateMedalPoppel(dossierDescr, affectingRecord, value, prevValue):
    medalPoppelCfg = RECORD_CONFIGS['medalPoppel']
    spotted = dossierDescr['spotted']
    maxMedalClass = len(medalPoppelCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if spotted >= medalPoppelCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalPoppel']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalPoppel'] = medalClass
    return


def _updateMedalAbrams(dossierDescr, affectingRecord, value, prevValue):
    medalAbramsCfg = RECORD_CONFIGS['medalAbrams']
    winAndSurvived = dossierDescr['winAndSurvived']
    maxMedalClass = len(medalAbramsCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if winAndSurvived >= medalAbramsCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalAbrams']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalAbrams'] = medalClass
    return


def _updateMedalLeClerc(dossierDescr, affectingRecord, value, prevValue):
    medalLeClercCfg = RECORD_CONFIGS['medalLeClerc']
    capturePoints = dossierDescr['capturePoints']
    maxMedalClass = len(medalLeClercCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if capturePoints >= medalLeClercCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalLeClerc']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalLeClerc'] = medalClass
    return


def _updateMedalLavrinenko(dossierDescr, affectingRecord, value, prevValue):
    medalLavrinenkoCfg = RECORD_CONFIGS['medalLavrinenko']
    droppedCapturePoints = dossierDescr['droppedCapturePoints']
    maxMedalClass = len(medalLavrinenkoCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if droppedCapturePoints >= medalLavrinenkoCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalLavrinenko']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalLavrinenko'] = medalClass
    return


def _updateMedalEkins(dossierDescr, affectingRecord, value, prevValue):
    medalEkinsCfg = RECORD_CONFIGS['medalEkins']
    frags = dossierDescr['frags8p']
    maxMedalClass = len(medalEkinsCfg)
    for medalClass in xrange(1, maxMedalClass + 1):
        if frags >= medalEkinsCfg[maxMedalClass - medalClass]:
            break
    else:
        return

    curClass = dossierDescr['medalEkins']
    if curClass == 0 or curClass > medalClass:
        dossierDescr['medalEkins'] = medalClass
    return


def _updateBeasthunter(dossierDescr, affectingRecord, value, prevValue):
    minFrags = RECORD_CONFIGS['beasthunter']
    if dossierDescr['fragsBeast'] >= minFrags:
        dossierDescr['beasthunter'] = 1
    return


def _updateMousebane(dossierDescr, affectingRecord, value, prevValue):
    minFrags = RECORD_CONFIGS['mousebane']
    mausFrags = dossierDescr['vehTypeFrags'].get(_g_cache['mausTypeCompDescr'], 0)
    (medals, series) = divmod(mausFrags, minFrags)
    if not series:
        dossierDescr['mousebane'] = medals
    return


def _updateTankExpert(dossierDescr, affectingRecord, value, prevValue):
    treeVehiclesFragged = 0
    for compactDescr in dossierDescr['vehTypeFrags'].iterkeys():
        if compactDescr in _g_cache['vehiclesInTrees']:
            treeVehiclesFragged += 1

    if treeVehiclesFragged == len(_g_cache['vehiclesInTrees']):
        dossierDescr['tankExpert'] = 1
    return


def _updateMaxSniperSeries(dossierDescr, affectingRecord, value, prevValue):
    maxSniperSeries = dossierDescr['maxSniperSeries']
    if dossierDescr['sniperSeries'] > maxSniperSeries:
        dossierDescr['maxSniperSeries'] = dossierDescr['sniperSeries']
    return


def _updateTitleSniper(dossierDescr, affectingRecord, value, prevValue):
    minLength = RECORD_CONFIGS['titleSniper']
    if dossierDescr['maxSniperSeries'] >= minLength:
        dossierDescr['titleSniper'] = 1
    return


def _updateMaxInvincibleSeries(dossierDescr, affectingRecord, value, prevValue):
    maxInvincibleSeries = dossierDescr['maxInvincibleSeries']
    if dossierDescr['invincibleSeries'] > maxInvincibleSeries:
        dossierDescr['maxInvincibleSeries'] = dossierDescr['invincibleSeries']
    return


def _updateInvincible(dossierDescr, affectingRecord, value, prevValue):
    minLength = RECORD_CONFIGS['invincible']
    if dossierDescr['maxInvincibleSeries'] >= minLength:
        dossierDescr['invincible'] = 1
    return


def _updateMaxDiehardSeries(dossierDescr, affectingRecord, value, prevValue):
    maxDiehardSeries = dossierDescr['maxDiehardSeries']
    if dossierDescr['diehardSeries'] > maxDiehardSeries:
        dossierDescr['maxDiehardSeries'] = dossierDescr['diehardSeries']
    return


def _updateDiehard(dossierDescr, affectingRecord, value, prevValue):
    minLength = RECORD_CONFIGS['diehard']
    if dossierDescr['maxDiehardSeries'] >= minLength:
        dossierDescr['diehard'] = 1
    return


def _updateMaxKillingSeries(dossierDescr, affectingRecord, value, prevValue):
    maxKillingSeries = dossierDescr['maxKillingSeries']
    if dossierDescr['killingSeries'] > maxKillingSeries:
        dossierDescr['maxKillingSeries'] = dossierDescr['killingSeries']
    return


def _updateHandOfDeath(dossierDescr, affectingRecord, value, prevValue):
    minLength = RECORD_CONFIGS['handOfDeath']
    if dossierDescr['maxKillingSeries'] >= minLength:
        dossierDescr['handOfDeath'] = 1
    return


def _updateMaxPiercingSeries(dossierDescr, affectingRecord, value, prevValue):
    maxPiercingSeries = dossierDescr['maxPiercingSeries']
    if dossierDescr['piercingSeries'] > maxPiercingSeries:
        dossierDescr['maxPiercingSeries'] = dossierDescr['piercingSeries']
    return


def _updateArmorPiercer(dossierDescr, affectingRecord, value, prevValue):
    minLength = RECORD_CONFIGS['armorPiercer']
    if dossierDescr['maxPiercingSeries'] >= minLength:
        dossierDescr['armorPiercer'] = 1
    return


def _updateLumberjack(dossierDescr, affectingRecord, value, prevValue):
    minTrees = RECORD_CONFIGS['lumberjack']
    if dossierDescr['treesCut'] >= minTrees:
        dossierDescr['lumberjack'] = 1
    return


def _getNewDossierData(version, recordsLayout, compDescr):
    data = {}
    for record in chain(*recordsLayout):
        data[record] = RECORD_DEFAULT_VALUES.get(record, 0)

    data['_version'] = version
    return data


def _getNewAccountDossierData(compDescr):
    data = _getNewDossierData(ACCOUNT_DOSSIER_VERSION, _ACCOUNT_RECORDS_LAYOUT, compDescr)
    data['creationTime'] = int(time.time())
    return data


def _set_ACCOUNT_DOSSIER_UPDATERS():
    global _ACCOUNT_DOSSIER_UPDATERS
    _ACCOUNT_DOSSIER_UPDATERS = {0: _getNewAccountDossierData}
    return


def _set_VEHICLE_DOSSIER_UPDATERS():
    global _VEHICLE_DOSSIER_UPDATERS
    _VEHICLE_DOSSIER_UPDATERS = {0: (partial(_getNewDossierData, VEHICLE_DOSSIER_VERSION, _VEHICLE_RECORDS_LAYOUT))}
    return


def _set_TANKMAN_DOSSIER_UPDATERS():
    global _TANKMAN_DOSSIER_UPDATERS
    _TANKMAN_DOSSIER_UPDATERS = {0: (partial(_getNewDossierData, TANKMAN_DOSSIER_VERSION, _TANKMAN_RECORDS_LAYOUT))}
    return


class _DossierDescr(object):

    def __init__(self, compDescr, recordsLayout, dynRecPosRecord, staticRecordPositions, staticRecordsFmt, dependencies, curVersion, versionUpdaters):
        if len(compDescr) < 2:
            self.__compDescr = '\x00\x00'
        else:
            self.__compDescr = compDescr
        self._recordsLayout = recordsLayout
        self.__dynRecPosRecord = dynRecPosRecord
        self.__staticRecordPositions = staticRecordPositions
        self.__staticRecordsFmt = staticRecordsFmt
        self.__dependencies = dependencies
        self.__curVersion = curVersion
        self.__versionUpdaters = versionUpdaters
        self.__isExpanded = False
        self.__dependentUpdates = 0
        self.__data = {}
        self.__changed = set([])
        self.notified = set([])
        self.__updateVersion()
        return

    def __getitem__(self, record):
        if record in self.__data:
            return self.__data[record]
        if record in self._recordsLayout[0]:
            packing = _RECORD_PACKING[record]
            position = self.__staticRecordPositions[record]
            values = unpack('<' + packing[1], self.__compDescr[position:position + packing[2]])
            if packing[0] == 'p':
                self.__data[record] = values[0]
            else:
                self.__data[record] = packing[5](values)
            return self.__data[record]
        packing = _RECORD_PACKING[record]
        dynRecIdx = self._recordsLayout[1].index(record)
        offset = self[self.__dynRecPosRecord][dynRecIdx]
        (self.__data[record], _) = packing[2](self.__compDescr, offset)
        return self.__data[record]

    def __setitem__(self, record, value):
        packing = _RECORD_PACKING[record]
        if packing[0] == 'p':
            value = min(value, packing[3])
        prevValue = self[record]
        if record in POP_UP_RECORDS:
            if value != prevValue or record in EVENT_RECORDS:
                self.notified.add(record)
        if value == prevValue:
            return
        self.__data[record] = value
        self.__changed.add(record)
        isFromOutside = self.__dependentUpdates == 0
        self.__dependentUpdates += 1
        if self.__dependentUpdates >= 100:
            LOG_ERROR('Too many subsequent updates of dependent records')
            return
        for updater in self.__dependencies[record]:
            updater(self, record, value, prevValue)

        if isFromOutside:
            self.__dependentUpdates = 0
        return

    def __iter__(self):
        return _DossierDescrIterator(self)

    def update(self, d):
        for (key, value) in d.iteritems():
            self[key] = value

        return

    def expand(self):
        if self.__isExpanded:
            return
        data = self.__data
        prevData = dict(data)
        fmt = self.__staticRecordsFmt
        offset = self.__staticRecordPositions['_staticRecordsSize']
        values = unpack(fmt, self.__compDescr[:offset])
        index = 0
        for record in self._recordsLayout[0]:
            packing = _RECORD_PACKING[record]
            if packing[0] == 'p':
                self.__data[record] = values[index]
                index += 1
            else:
                self.__data[record] = packing[5](values[index:index + packing[3]])
                index += packing[3]

        for record in self._recordsLayout[1]:
            packing = _RECORD_PACKING[record]
            (self.__data[record], offset) = packing[2](self.__compDescr, offset)

        data.update(prevData)
        self.__isExpanded = True
        return

    def makeCompDescr(self):
        if not self.__changed:
            return self.__compDescr
        data = self.__data
        if self.__isExpanded:
            dynRecordsFmt = ''
            dynRecordsValues = []
            dynRecOffset = self.__staticRecordPositions['_staticRecordsSize']
            dynRecPos = list(data[self.__dynRecPosRecord])
            for i in xrange(len(self._recordsLayout[1])):
                record = self._recordsLayout[1][i]
                packing = _RECORD_PACKING[record]
                dynRecPos[i] = dynRecOffset
                (recFmt, recValues, recSize) = packing[1](data[record])
                dynRecordsFmt += recFmt
                dynRecordsValues += recValues
                dynRecOffset += recSize

            data[self.__dynRecPosRecord] = dynRecPos
            fmt = '<'
            values = []
            for record in self._recordsLayout[0]:
                packing = _RECORD_PACKING[record]
                fmt += packing[1]
                if packing[0] == 'p':
                    values.append(data[record])
                else:
                    values += packing[4](data[record])

            self.__compDescr = pack((fmt + dynRecordsFmt), *(values + dynRecordsValues))
        else:
            while self.__changed:
                changed = list(self.__changed)
                self.__changed.clear()
                for record in changed:
                    packing = _RECORD_PACKING[record]
                    if packing[0] == 'p':
                        substr = pack('<' + packing[1], data[record])
                        prevSize = packing[2]
                        position = self.__staticRecordPositions[record]
                    elif packing[0] == 's':
                        substr = pack(('<' + packing[1]), *packing[4](data[record]))
                        prevSize = packing[2]
                        position = self.__staticRecordPositions[record]
                    else:
                        (fmt, values, size) = packing[1](data[record])
                        substr = pack(('<' + fmt), *values)
                        dynRecIdx = self._recordsLayout[1].index(record)
                        dynRecPosExt = self[self.__dynRecPosRecord] + (len(self.__compDescr),)
                        position = dynRecPosExt[dynRecIdx]
                        prevSize = dynRecPosExt[dynRecIdx + 1] - position
                        sizeDiff = size - prevSize
                        if sizeDiff != 0:
                            dynRecPos = list(dynRecPosExt[:-1])
                            for i in xrange(dynRecIdx + 1, len(self._recordsLayout[1])):
                                dynRecPos[i] += sizeDiff

                            self[self.__dynRecPosRecord] = tuple(dynRecPos)
                    self.__compDescr = self.__compDescr[:position] + substr + self.__compDescr[position + prevSize:]

            self.__changed.clear()
            self.notified.clear()
        return self.__compDescr

    def __updateVersion(self):
        while True:
            ver = self['_version']
            if ver == self.__curVersion:
                break
            updater = self.__versionUpdaters.get(ver, self.__versionUpdaters[0])
            self.__data = updater(self.__compDescr)
            self.__changed.add('_version')
            self.__isExpanded = True

        return


class _DossierDescrIterator(object):

    def __init__(self, dossierDescr):
        self.__dossierDescr = dossierDescr
        self.__dossierDescr.expand()
        self.__recordNames = dossierDescr._recordsLayout[0] + dossierDescr._recordsLayout[1]
        self.__recordIdx = 0
        return

    def next(self):
        if self.__recordIdx >= len(self.__recordNames):
            raise StopIteration
        record = self.__recordNames[self.__recordIdx]
        self.__recordIdx += 1
        return (record, self.__dossierDescr[record])


def init():
    global _g_cache
    _set_RECORD_PACKING()
    _set_RECORD_DEPENDENCIES()
    _set_ACCOUNT_RECORD_DEPENDENCIES()
    _set_VEHICLE_RECORD_DEPENDENCIES()
    _set_TANKMAN_RECORD_DEPENDENCIES()
    _set_ACCOUNT_RECORDS_LAYOUT()
    _set_VEHICLE_RECORDS_LAYOUT()
    _set_TANKMAN_RECORDS_LAYOUT()
    _set_STATIC_RECORD_POSITIONS()
    _set_STATIC_RECORDS_FMT()
    _set_ACCOUNT_DOSSIER_UPDATERS()
    _set_VEHICLE_DOSSIER_UPDATERS()
    _set_TANKMAN_DOSSIER_UPDATERS()
    _g_cache = _buildCache()
    return


def checkIntegrity():
    for record in RECORD_NAMES[1:]:
        if record not in _RECORD_PACKING:
            LOG_WARNING('Packing for the record is not specified', record)

    for record in chain(*_ACCOUNT_RECORDS_LAYOUT):
        if record not in RECORD_NAMES and record[0] != '_':
            LOG_WARNING('Layout containg record that is not in the list', record)

    for record in chain(*_VEHICLE_RECORDS_LAYOUT):
        if record not in RECORD_NAMES and record[0] != '_':
            LOG_WARNING('Layout containg record that is not in the list', record)

    for record in chain(*_TANKMAN_RECORDS_LAYOUT):
        if record not in RECORD_NAMES and record[0] != '_':
            LOG_WARNING('Layout containg record that is not in the list', record)

    for (record, packing) in _RECORD_PACKING.iteritems():
        if packing[0] != 'p' and record not in RECORD_DEFAULT_VALUES:
            LOG_WARNING('Default value is not specified for the record', record)

    return


def _buildCache():
    vehicles8p = set()
    beastVehicles = set()
    vehiclesInTrees = set()
    unlocksSources = vehicles.getUnlocksSources()
    for nationIdx in xrange(len(nations.NAMES)):
        nationList = vehicles.g_list.getList(nationIdx)
        for vehDescr in nationList.itervalues():
            if vehDescr['level'] >= 8:
                vehicles8p.add(vehDescr['compactDescr'])
            if 'beast' in vehDescr['tags']:
                beastVehicles.add(vehDescr['compactDescr'])
            if len(unlocksSources.get(vehDescr['compactDescr'], set())) > 0 or len(vehicles.g_cache.vehicle(nationIdx, vehDescr['id']).unlocksDescrs) > 0:
                vehiclesInTrees.add(vehDescr['compactDescr'])

    return {'vehicles8+': vehicles8p, 
       'beastVehicles': beastVehicles, 
       'mausTypeCompDescr': (vehicles.makeIntCompactDescrByID('vehicle', *vehicles.g_list.getIDsByName('germany:Maus'))), 
       'vehiclesInTrees': vehiclesInTrees}


return
