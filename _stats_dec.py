# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/account_helpers/Stats.py
# Compiled at: 2011-05-26 15:49:28
import AccountCommands, time, constants, items, dossiers
from functools import partial
from PlayerEvents import g_playerEvents as events
from itertools import izip
from items import vehicles
from constants import DOSSIER_TYPE, ACCOUNT_ATTR
from AccountCommands import VEHICLE_SETTINGS_FLAG
from debug_utils import *
_VEHICLE = items.ITEM_TYPE_INDICES['vehicle']
_CHASSIS = items.ITEM_TYPE_INDICES['vehicleChassis']
_TURRET = items.ITEM_TYPE_INDICES['vehicleTurret']
_GUN = items.ITEM_TYPE_INDICES['vehicleGun']
_ENGINE = items.ITEM_TYPE_INDICES['vehicleEngine']
_FUEL_TANK = items.ITEM_TYPE_INDICES['vehicleFuelTank']
_RADIO = items.ITEM_TYPE_INDICES['vehicleRadio']
_TANKMAN = items.ITEM_TYPE_INDICES['tankman']
_OPTIONALDEVICE = items.ITEM_TYPE_INDICES['optionalDevice']
_SHELL = items.ITEM_TYPE_INDICES['shell']
_EQUIPMENT = items.ITEM_TYPE_INDICES['equipment']
_SIMPLE_VALUE_STATS = ('credits', 'gold', 'slots', 'berths', 'freeXP', 'dossier', 'clanInfo',
                       'accOnline', 'accOffline', 'freeTMenLeft', 'freeVehiclesLeft',
                       'captchaTriesLeft', 'hasFinPassword', 'tkillIsSuspected')
_DICT_STATS = ('vehTypeXP', )
_GROWING_SET_STATS = ('unlocks', 'eliteVehicles', 'doubleXPVehs')
_ACCOUNT_STATS = ('clanDBID', 'attrs', 'premiumExpiryTime', 'autoBanTime', 'restrictions')
_CACHE_STATS = ('battlesTillCaptcha', )

class Stats(object):

    def __init__(self, syncData):
        self.__account = None
        self.__syncData = syncData
        self.__cache = {}
        self.__ignore = True
        return

    def onAccountBecomePlayer(self):
        self.__ignore = False
        return

    def onAccountBecomeNonPlayer(self):
        self.__ignore = True
        return

    def setAccount(self, account):
        self.__account = account
        return

    def synchronize(self, isFullSync, diff):
        if isFullSync:
            self.__cache.clear()
        cache = self.__cache
        statsDiff = diff.get('stats', None)
        if statsDiff is not None:
            for stat in _SIMPLE_VALUE_STATS:
                if stat in statsDiff:
                    cache[stat] = statsDiff[stat]

            for stat in _DICT_STATS:
                if stat in statsDiff:
                    cacheDict = cache.setdefault(stat, dict())
                    for (key, value) in statsDiff[stat].iteritems():
                        if value is None:
                            cacheDict.pop(key, None)
                        else:
                            cacheDict[key] = value

            for stat in _GROWING_SET_STATS:
                stat_r = stat + '_r'
                if stat_r in statsDiff:
                    cache[stat] = statsDiff[stat_r]
                if stat in statsDiff:
                    cache.setdefault(stat, set()).update(statsDiff[stat])

        cacheDiff = diff.get('cache', None)
        if cacheDiff is not None:
            for stat in _CACHE_STATS:
                if stat in cacheDiff:
                    cache[stat] = cacheDiff[stat]

        accountDiff = diff.get('account', None)
        if accountDiff is not None:
            for stat in _ACCOUNT_STATS:
                if stat in accountDiff:
                    cache[stat] = accountDiff[stat]

            if 'unlockedVehicleLevel' in accountDiff:
                self.__sync_unlockedVehicleLevel(accountDiff['unlockedVehicleLevel'])
        economicsDiff = diff.get('economics', None)
        if economicsDiff is not None:
            for stat in ('unlocks', 'eliteVehicles'):
                if stat in economicsDiff:
                    cache.setdefault(stat, set()).update(economicsDiff[stat])

        return

    def get(self, statName, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, None)
            return
        else:
            self.__syncData.waitForSync(partial(self.__onGetResponse, statName, callback))
            return

    def unlock(self, vehTypeCompDescr, unlockIdx, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_UNLOCK, vehTypeCompDescr, unlockIdx, 0, proxy)
            return

    def setCurrentVehicle(self, vehInvID, callback=None):
        LOG_WARNING('Deprecated. setCurrentVehicle')
        return

    def exchange(self, gold, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            self.__account.shop.getExchangeRate(partial(self.__exchange_onGetRate, gold, callback))
            return

    def convertToFreeXP(self, vehTypeCompDescrs, xp, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            self.__account.shop.getFreeXPConversion(partial(self.__convertToFreeXP_onGetParameters, vehTypeCompDescrs, xp, callback))
            return

    def upgradeToPremium(self, days, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0)
            return
        else:
            self.__account.shop.getPremiumCost(partial(self.__premium_onGetPremCost, days, callback))
            return

    def buySlot(self, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0)
            return
        else:
            self.__account.shop.waitForSync(partial(self.__slot_onShopSynced, callback))
            return

    def buyBerths(self, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0)
            return
        else:
            self.__account.shop.waitForSync(partial(self.__berths_onShopSynced, callback))
            return

    def setMoney(self, credits, gold=0, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_SET_MONEY, credits, gold, 0, proxy)
            return

    def addExperience(self, vehTypeName, xp, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            vehTypeCompDescr = vehicles.makeIntCompactDescrByID('vehicle', *vehicles.g_list.getIDsByName(vehTypeName))
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_ADD_XP, vehTypeCompDescr, xp, 0, proxy)
            return

    def unlockAll(self, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_UNLOCK_ALL, 0, 0, 0, proxy)
            return

    def __sync_unlockedVehicleLevel(self, level):
        if level <= 0:
            return
        import nations
        unlocks = self.__cache.setdefault('unlocks', set())
        eliteVehicles = self.__cache.setdefault('eliteVehicles', set())
        for nationID in xrange(len(nations.NAMES)):
            for vehTypeID in vehicles.g_list.getList(nationID):
                vehType = vehicles.g_cache.vehicle(nationID, vehTypeID)
                if vehType.level > level:
                    continue
                eliteVehicles.add(vehType.compactDescr)
                unlocks.add(vehType.compactDescr)
                unlocks.update(vehType.autounlockedItems)
                unlocks.update([_[1] for d in vehType.unlocksDescrs if vehicles.parseIntCompactDescr(d[1])[0] != _VEHICLE])

        return

    def __onGetResponse(self, statName, callback, resultID):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None)
            return
        else:
            statValue = self.__cache.get(statName, None)
            if callback is not None:
                callback(resultID, statValue)
            return

    def __exchange_onGetRate(self, gold, callback, resultID, exchRate, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None)
            return
        else:
            if exchRate is None:
                LOG_ERROR('Result of the getExchangeRate request is None')
                if callback is not None:
                    callback(AccountCommands.RES_FAILURE)
                return
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_EXCHANGE, shopRev, gold, 0, proxy)
            return

    def __convertToFreeXP_onGetParameters(self, vehTypeCompDescrs, xp, callback, resultID, freeXPConversion, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None)
            return
        else:
            if freeXPConversion is None:
                LOG_ERROR('Result of the getFreeXPConversion request is None')
                if callback is not None:
                    callback(AccountCommands.RES_FAILURE)
                return
            arr = [
             shopRev, xp] + list(vehTypeCompDescrs)
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdIntArr(AccountCommands.CMD_FREE_XP_CONV, arr, proxy)
            return

    def __premium_onGetPremCost(self, days, callback, resultID, premCost, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None)
            return
        else:
            if premCost is None:
                LOG_ERROR('Result of the getPremiumCost request is None')
                if callback is not None:
                    callback(AccountCommands.RES_FAILURE)
                return
            gold = premCost.get(days, None)
            if gold is None:
                LOG_ERROR('Wrong days number')
                if callback is not None:
                    callback(AccountCommands.RES_WRONG_ARGS)
                return
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_PREMIUM, shopRev, days, 0, proxy)
            return

    def __slot_onShopSynced(self, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_BUY_SLOT, shopRev, 0, 0, proxy)
            return

    def __berths_onShopSynced(self, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_BUY_BERTHS, shopRev, 0, 0, proxy)
            return


return

# okay decompiling C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\account_helpers\Stats.pyc
