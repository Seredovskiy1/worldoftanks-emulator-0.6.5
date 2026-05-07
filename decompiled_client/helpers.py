# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/common/dossiers/helpers.py
# Compiled at: 2011-05-26 15:49:22
import time, battle_heroes, arena_achievements, dossiers
from constants import DOSSIER_TYPE, ARENA_BONUS_TYPE
from itertools import izip
from debug_utils import *

def updateDossier(descr, dossierType, battleResults, resultsOverriding=None, vehTypeCompDescr=None, relatedDossier=None):
    isAccount = dossierType & DOSSIER_TYPE.ACCOUNT
    isTankman = dossierType & DOSSIER_TYPE.TANKMAN
    isVehicle = dossierType & DOSSIER_TYPE.VEHICLE
    assert not isAccount or vehTypeCompDescr is not None
    if resultsOverriding is None:
        results = battleResults
    else:
        results = dict(battleResults)
        results.update(resultsOverriding)
    descr['battleLifeTime'] += results['lifeTime']
    if results['bonusType'] not in (
     ARENA_BONUS_TYPE.REGULAR, ARENA_BONUS_TYPE.COMPANY, ARENA_BONUS_TYPE.CLAN):
        return set()
    else:
        descr['lastBattleTime'] = int(time.time())
        descr['battlesCount'] += 1
        xpFactor = results['xpFactor']
        if xpFactor != 0:
            xp = int(results['xp'] / xpFactor)
        else:
            xp = 0
        if xp != 0:
            descr['xp'] += xp
            if xp >= descr['maxXP']:
                descr['maxXP'] = xp
                if isAccount:
                    descr['maxXPVehicle'] = vehTypeCompDescr
        isWinner = results['isWinner']
        if isWinner == 1:
            descr['wins'] += 1
        elif isWinner == -1:
            descr['losses'] += 1
        if isTankman:
            if results['isActive']:
                descr['survivedBattles'] += 1
        elif results['killerID'] == 0:
            descr['survivedBattles'] += 1
            if isWinner == 1:
                descr['winAndSurvived'] += 1
        ownVehicleID = results['vehicleID']
        ownAchieveIndices = [_[1] for (code, vehicleID) in izip(results['achieveIndices'], results['heroVehicleIDs']) if vehicleID == ownVehicleID]
        for achieveIdx in ownAchieveIndices:
            record = battle_heroes.ACHIEVEMENT_NAMES[achieveIdx]
            descr[record] += 1

        for achieveIdx in results['epicAchievements']:
            record = arena_achievements.ACHIEVEMENT_NAMES[achieveIdx]
            descr[record] += 1

        for achieveName in ('raider', 'kamikaze'):
            achieveIdx = arena_achievements.ACHIEVEMENTS_INDICES[achieveName]
            if achieveIdx << 24 in results['honorTitles']:
                descr[achieveName] += 1

        perBattleSeriesAchievementNames = ('invincible', 'diehard')
        perBattleSeriesAchievementBestResults = ('maxInvincibleSeries', 'maxDiehardSeries')
        if isVehicle:
            for achieveName in perBattleSeriesAchievementNames:
                achieveIdx = arena_achievements.ACHIEVEMENTS_INDICES[achieveName]
                recordName = achieveName + 'Series'
                if achieveIdx << 24 in results['honorTitles']:
                    descr[recordName] += 1
                else:
                    descr[recordName] = 0

        elif isAccount:
            assert relatedDossier is not None
            for recordName in perBattleSeriesAchievementBestResults:
                if relatedDossier[recordName] > descr[recordName]:
                    descr[recordName] = relatedDossier[recordName]

        seriesAchievementNames = ('sniper', 'killing', 'piercing')
        seriesAchievementBestResults = ('maxSniperSeries', 'maxKillingSeries', 'maxPiercingSeries')
        if isVehicle:
            for achieveName in seriesAchievementNames:
                achieveIdx = arena_achievements.ACHIEVEMENTS_INDICES[achieveName]
                recordName = achieveName + 'Series'
                series = [_[2] for code in results['honorTitles'] if code >> 24 == achieveIdx]
                if series:
                    descr[recordName] = descr[recordName] + series[0]
                for runLength in series[1:]:
                    descr[recordName] = runLength

        elif isAccount:
            assert relatedDossier is not None
            for recordName in seriesAchievementBestResults:
                if relatedDossier[recordName] > descr[recordName]:
                    descr[recordName] = relatedDossier[recordName]

        shots = results['shots']
        if shots != 0:
            descr['shots'] += shots
        hits = results['hits']
        if hits != 0:
            descr['hits'] += hits
        spotted = results['spotted']
        if spotted:
            descr['spotted'] += len(spotted)
        damageDealt = results['damageDealt']
        if damageDealt != 0:
            descr['damageDealt'] += damageDealt
        damageReceived = results['damageReceived']
        if damageReceived != 0:
            descr['damageReceived'] += damageReceived
        capturePoints = results['capturePoints']
        if capturePoints != 0:
            descr['capturePoints'] += capturePoints
        droppedCapturePoints = min(results['droppedCapturePoints'], 100)
        if droppedCapturePoints != 0:
            descr['droppedCapturePoints'] += droppedCapturePoints
        killedTypeCompDescrs = results['killedTypeCompDescrs']
        if killedTypeCompDescrs:
            if isTankman:
                vehTypeFrags = {}
            else:
                vehTypeFrags = dict(descr['vehTypeFrags'])
            vehicles8p = dossiers._g_cache['vehicles8+']
            beastVehicles = dossiers._g_cache['beastVehicles']
            frags8p = 0
            fragsBeast = 0
            for vtcd in killedTypeCompDescrs:
                frags = vehTypeFrags.get(vtcd, 0)
                vehTypeFrags[vtcd] = min(frags + 1, 60001)
                if vtcd in vehicles8p:
                    frags8p += 1
                if vtcd in beastVehicles:
                    fragsBeast += 1

            if not isTankman:
                descr['vehTypeFrags'] = vehTypeFrags
            frags = len(killedTypeCompDescrs)
            descr['frags'] += frags
            if frags8p != 0:
                descr['frags8p'] += frags8p
            if fragsBeast != 0:
                descr['fragsBeast'] += fragsBeast
            if frags >= descr['maxFrags']:
                descr['maxFrags'] = frags
                if isAccount:
                    descr['maxFragsVehicle'] = vehTypeCompDescr
        if isAccount:
            vehDossiersCut = dict(descr['vehDossiersCut'])
            (battlesCount, wins) = vehDossiersCut.get(vehTypeCompDescr, (0, 0))
            if isWinner == 1:
                wins += 1
            vehDossiersCut[vehTypeCompDescr] = (
             battlesCount + 1, wins)
            descr['vehDossiersCut'] = vehDossiersCut
            return set(descr.notified)
        if isVehicle:
            return set(descr.notified) & dossiers.EVENT_RECORDS
        return set()
        return


return
