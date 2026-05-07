# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/gui/Scaleform/utils/dossiers_utils.py
# Compiled at: 2011-05-26 15:49:26
import BigWorld
from items.vehicles import getVehicleType
from helpers.i18n import makeString
import dossiers
from gui.Scaleform.utils.gui_items import makeTooltip
from constants import DOSSIER_TYPE
TOTAL_BLOCKS = (
 (
  'common', ('battlesCount', 'wins', 'losses', 'survivedBattles')),
 (
  'battleeffect', ('frags', 'maxFrags', 'effectiveShots', 'damageDealt')),
 (
  'credits', ('xp', 'avgExperience', 'maxXP')))
VEHICLE_BLOCKS = (
 (
  'common', ('battlesCount', 'wins', 'losses', 'survivedBattles')),
 (
  'battleeffect', ('frags', 'maxFrags', 'effectiveShots', 'damageDealt')),
 (
  'credits', ('xp', 'avgExperience', 'maxXP')))
MEDALS_BLOCKS = (
 ('warrior', 'invader', 'sniper', 'defender', 'steelwall', 'supporter', 'scout'),
 ('medalKay', 'medalCarius', 'medalKnispel', 'medalPoppel', 'medalAbrams', 'medalLeClerc',
 'medalLavrinenko', 'medalEkins'),
 ('medalWittmann', 'medalOrlik', 'medalOskin', 'medalHalonen', 'medalBurda', 'medalBillotte',
 'medalKolobanov', 'medalFadin'),
 ('beasthunter', 'mousebane', 'tankExpert', 'titleSniper', 'invincible', 'diehard',
 'raider', 'handOfDeath', 'armorPiercer', 'kamikaze', 'lumberjack'))
TANKMEN_MEDALS_BLOCKS = (
 ('warrior', 'invader', 'sniper', 'defender', 'steelwall', 'supporter', 'scout'),
 ('medalKay', 'medalCarius', 'medalKnispel', 'medalPoppel', 'medalAbrams', 'medalLeClerc',
 'medalLavrinenko', 'medalEkins'),
 ('medalWittmann', 'medalOrlik', 'medalOskin', 'medalHalonen', 'medalBurda', 'medalBillotte',
 'medalKolobanov', 'medalFadin'),
 ('beasthunter', 'titleSniper', 'invincible', 'diehard', 'raider', 'handOfDeath', 'armorPiercer',
 'kamikaze', 'lumberjack'))
MEDALS_UNIC_FOR_RANK = ('medalKay', 'medalCarius', 'medalKnispel', 'medalPoppel', 'medalAbrams',
                        'medalLeClerc', 'medalLavrinenko', 'medalEkins')
MEDALS_TITLES = ('beasthunter', 'tankExpert', 'lumberjack')
MEDALS_SERIES = {'titleSniper': {'cur': 'sniperSeries', 
                   'max': 'maxSniperSeries', 
                   'format': ('maxSniperSeries', )}, 
   'invincible': {'cur': 'invincibleSeries', 
                  'max': 'maxInvincibleSeries', 
                  'format': ('maxInvincibleSeries', )}, 
   'diehard': {'cur': 'diehardSeries', 
               'max': 'maxDiehardSeries', 
               'format': ('maxDiehardSeries', )}, 
   'handOfDeath': {'cur': 'killingSeries', 
                   'max': 'maxKillingSeries', 
                   'format': ('maxKillingSeries', )}, 
   'armorPiercer': {'cur': 'piercingSeries', 
                    'max': 'maxPiercingSeries', 
                    'format': ('maxPiercingSeries', )}}
_ICONS_MASK = '../maps/icons/vehicle/small/%s.tga'

def getDossierVehicleList(dossier, isOnlyTotal=False):
    data = [
     'ALL',
     '#menu:profile/list/totalName',
     _ICONS_MASK % 'all',
     0,
     -1,
     __getData('battlesCount', dossier),
     __getData('wins', dossier)]
    if not isOnlyTotal:
        vehList = dossier['vehDossiersCut'].items()
        vehList.sort(cmp=__dossierComparator)
        for (vehTypeCompactDesr, battles) in vehList:
            vehType = getVehicleType(vehTypeCompactDesr)
            data.append(vehTypeCompactDesr)
            data.append(vehType.userString)
            data.append(_ICONS_MASK % vehType.name.replace(':', '-'))
            data.append(vehType.level)
            data.append(vehType.id[0])
            data.append(battles[0])
            data.append(battles[1])

    return data


def getDossierMedals(dossier, dossier_type=DOSSIER_TYPE.ACCOUNT):
    medals = []
    blocks = []
    if dossier_type == DOSSIER_TYPE.ACCOUNT:
        blocks = MEDALS_BLOCKS
    elif dossier_type == DOSSIER_TYPE.TANKMAN:
        blocks = TANKMEN_MEDALS_BLOCKS
    for group in blocks:
        for type in group:
            if dossier[type]:
                medals.append(type)
                max_value = dossiers.getRecordMaxValue(type)
                if type in MEDALS_UNIC_FOR_RANK:
                    medals.append(dossier[type])
                elif type in MEDALS_SERIES.keys():
                    medals.append(dossier[MEDALS_SERIES[type]['max']])
                elif dossier[type] >= max_value:
                    medals.append(makeString('#achievements:achievement/maxMedalValue') % (max_value - 1))
                else:
                    medals.append(dossier[type])
                medals.append(type in MEDALS_UNIC_FOR_RANK)
                medals.append(type in MEDALS_TITLES)
                achiev_name = makeString('#achievements:%s' % type)
                if type in MEDALS_UNIC_FOR_RANK:
                    achiev_name = achiev_name % makeString('#achievements:achievement/rank%d' % dossier[type])
                achiev_tooltip_body = None
                if type in MEDALS_SERIES.keys():
                    format_args = []
                    for arg_type in MEDALS_SERIES[type].get('format', []):
                        format_args.append(dossier[arg_type])

                    achiev_tooltip_body = makeString('#tooltips:achievement/%s/body' % type) % tuple(format_args)
                medals.append(makeTooltip(achiev_name, achiev_tooltip_body, makeString('#tooltips:achievement/note')))
                medals.append(makeString('#achievements:%s_descr' % type))
                medals.append(False)

        if len(medals) != 0:
            medals[-1] = blocks[-1] != group

    return medals


def getMedal(achievementType, rank):
    medal = []
    for group in MEDALS_BLOCKS:
        for type in group:
            if type == achievementType:
                medal.append(type)
                medal.append(rank)
                medal.append(type in MEDALS_UNIC_FOR_RANK)
                tooltip = makeString('#achievements:%s' % type)
                medal.append(tooltip % makeString('#achievements:achievement/rank%d' % rank) if type in MEDALS_UNIC_FOR_RANK else tooltip)
                medal.append(makeString('#achievements:%s_descr' % type))

    return medal


def getDossierTotalBlocks(dossier):
    data = [
     '#menu:profile/list/totalName', len(TOTAL_BLOCKS)]
    for (blockType, fields) in TOTAL_BLOCKS:
        data.append(blockType)
        data.append(len(fields))
        for fieldType in fields:
            data.append(fieldType)
            data.append(__getData(fieldType, dossier))
            data.append(__getDataExtra(blockType, fieldType, dossier, True))

    return data


def getDossierVehicleBlocks(dossier, vehTypeId):
    vehType = getVehicleType(int(vehTypeId))
    data = [makeString('#menu:profile/list/descr', vehType.userString), len(VEHICLE_BLOCKS)]
    for (blockType, fields) in VEHICLE_BLOCKS:
        data.append(blockType)
        data.append(len(fields))
        for fieldType in fields:
            data.append(fieldType)
            data.append(__getData(fieldType, dossier))
            data.append(__getDataExtra(blockType, fieldType, dossier))

    return data


def __getData(fieldType, dossier):
    if fieldType == 'effectiveShots':
        if dossier['shots'] != 0:
            return '%d%%' % round(float(dossier['hits']) / dossier['shots'] * 100)
        return '0%'
    if fieldType == 'avgExperience':
        if dossier['battlesCount'] != 0:
            return BigWorld.wg_getIntegralFormat(round(float(dossier['xp']) / dossier['battlesCount']))
        return 0
    return BigWorld.wg_getIntegralFormat(dossier[fieldType])


def __getDataExtra(blockType, fieldType, dossier, isTotal=False):
    extra = ''
    if blockType == 'common':
        if fieldType != 'battlesCount' and dossier['battlesCount'] != 0:
            extra = '(%d%%)' % round(float(dossier[fieldType]) / dossier['battlesCount'] * 100)
    if isTotal:
        if fieldType == 'maxFrags' and dossier['maxFrags'] != 0:
            extra = getVehicleType(dossier['maxFragsVehicle']).userString
        if fieldType == 'maxXP' and dossier['maxXP'] != 0:
            extra = getVehicleType(dossier['maxXPVehicle']).userString
    return extra


def __dossierComparator(x1, x2):
    if x1[1][0] < x2[1][0]:
        return 1
    if x1[1][0] > x2[1][0]:
        return -1
    if x1[1][1] < x2[1][1]:
        return 1
    if x1[1][1] > x2[1][1]:
        return -1
    return 0


return
