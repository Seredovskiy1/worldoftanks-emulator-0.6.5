# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/account_helpers/Inventory.py
# Compiled at: 2011-05-26 15:49:28
import AccountCommands, items, dossiers, collections
from functools import partial
from PlayerEvents import g_playerEvents as events
from items import vehicles, tankmen
from constants import DOSSIER_TYPE
from AccountCommands import LOCK_REASON, SELL_VEHICLE_FLAG, VEHICLE_SETTINGS_FLAG
from debug_utils import *
from account_shared import AmmoIterator, getAmmoDiff
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

def getAmmoAsDict(ammo):
    ammoAsDict = collections.defaultdict(int)
    for i in xrange(len(ammo) / 2):
        ammoAsDict[ammo[2 * i]] += ammo[2 * i + 1]

    return ammoAsDict


class Inventory(object):

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
        invDiff = diff.get('inventory', None)
        if invDiff is not None:
            for (itemTypeIdx, itemInvDiff) in invDiff.iteritems():
                itemInvCache = self.__cache.setdefault(itemTypeIdx, {})
                self.__synchronizeDicts(itemInvDiff, itemInvCache)

        cacheDiff = diff.get('cache', None)
        if cacheDiff is not None:
            vehsLockDiff = cacheDiff.get('vehsLock', None)
            if vehsLockDiff is not None:
                itemInvCache = self.__cache.setdefault(_VEHICLE, {})
                vehsLockCache = itemInvCache.setdefault('lock', {})
                self.__synchronizeDicts(vehsLockDiff, vehsLockCache)
        return

    def getItems(self, itemTypeIdx, callback):
        self.__syncData.waitForSync(partial(self.__onGetItemsResponse, itemTypeIdx, callback))
        return

    def sell(self, itemTypeIdx, itemInvID, count, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if itemTypeIdx == _VEHICLE:
                self.sellVehicle(itemInvID, False, False, True, callback)
                return
            if itemTypeIdx == _TANKMAN:
                if callback is not None:
                    callback(AccountCommands.RES_WRONG_ARGS)
                return
            self.__account.shop.waitForSync(partial(self.__sellItem_onShopSynced, itemTypeIdx, itemInvID, count, callback))
            return

    def sellVehicle(self, vehInvID, sellShells, sellEquipments, dismissCrew, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            flags = SELL_VEHICLE_FLAG.NONE
            if dismissCrew:
                flags |= SELL_VEHICLE_FLAG.CREW
            if sellShells:
                flags |= SELL_VEHICLE_FLAG.SHELLS
            if sellEquipments:
                flags |= SELL_VEHICLE_FLAG.EQS
            self.__account.shop.waitForSync(partial(self.__sellVehicle_onShopSynced, vehInvID, flags, callback))
            return

    def dismissTankman(self, tmanInvID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_DISMISS_TMAN, tmanInvID, 0, 0, proxy)
            return

    def equip(self, vehInvID, itemCompDescr, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0, [])
            return
        else:
            itemTypeIdx = vehicles.parseIntCompactDescr(itemCompDescr)[0]
            assert itemTypeIdx in (_CHASSIS, _GUN, _ENGINE, _FUEL_TANK, _RADIO)
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_EQUIP, vehInvID, itemCompDescr, 0, proxy)
            return

    def equipTurret(self, vehInvID, turretCompDescr, gunCompDescr, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0, [])
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_EQUIP, vehInvID, turretCompDescr, gunCompDescr, proxy)
            return

    def equipOptionalDevice(self, vehInvID, deviceCompDescr, slotIdx, isPaidRemoval, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, 0, [])
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt4(AccountCommands.CMD_EQUIP_OPTDEV, vehInvID, deviceCompDescr, slotIdx, int(isPaidRemoval), proxy)
            return

    def equipShells(self, vehInvID, shells, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, [])
            return
        else:
            arr = [
             vehInvID] + [_[1] for s in shells]
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdIntArr(AccountCommands.CMD_EQUIP_SHELLS, arr, proxy)
            return

    def equipEquipments(self, vehInvID, eqs, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, [])
            return
        else:
            arr = [
             vehInvID] + [_[1] for e in eqs]
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdIntArr(AccountCommands.CMD_EQUIP_EQS, arr, proxy)
            return

    def equipTankman(self, vehInvID, slot, tmanInvID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, [])
            return
        else:
            if tmanInvID is None:
                tmanInvID = -1
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_EQUIP_TMAN, vehInvID, slot, tmanInvID, proxy)
            return

    def repair(self, vehInvID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_REPAIR, vehInvID, 0, 0, proxy)
            return

    def addTankmanSkill(self, tmanInvID, skillName, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            skillIdx = tankmen.SKILL_INDICES[skillName]
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_TMAN_ADD_SKILL, tmanInvID, skillIdx, 0, proxy)
            return

    def dropTankmanSkill(self, tmanInvID, skillName, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            skillIdx = tankmen.SKILL_INDICES[skillName]
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_TMAN_DROP_SKILL, tmanInvID, skillIdx, 0, proxy)
            return

    def respecTankman(self, tmanInvID, vehTypeCompDescr, tmanCostTypeIdx, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if vehTypeCompDescr is None:
                vehTypeCompDescr = 0
            self.__account.shop.waitForSync(partial(self.__respecTman_onShopSynced, tmanInvID, vehTypeCompDescr, tmanCostTypeIdx, callback))
            return

    def replacePassport(self, tmanInvID, isFemale, firstNameID, lastNameID, iconID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            self.__account.shop.waitForSync(partial(self.__replacePassport_onShopSynced, tmanInvID, isFemale, firstNameID, lastNameID, iconID, callback))
            return

    def changeVehicleSetting(self, vehInvID, setting, isOn, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            isOn = 1 if isOn else 0
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_VEH_SETTINGS, vehInvID, setting, isOn, proxy)
            return

    def changeVehicleCamouflage(self, vehInvID, camouflageID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            self.__account.shop.waitForSync(partial(self.__changeVehCamouflage_onShopSynced, vehInvID, camouflageID, callback))
            return

    def changeVehicleHorn(self, vehInvID, hornID, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            self.__account.shop.waitForSync(partial(self.__changeVehHorn_onShopSynced, vehInvID, hornID, callback))
            return

    def addTankmanExperience(self, tmanInvID, xp, callback=None):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_ADD_TMAN_XP, tmanInvID, xp, 0, proxy)
            return

    def __onGetItemsResponse(self, itemTypeIdx, callback, resultID):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None)
            return
        else:
            items = self.__cache.get(itemTypeIdx, None)
            if callback is not None:
                callback(resultID, items)
            return

    def __sellItem_onShopSynced(self, itemTypeIdx, itemInvID, count, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt4(AccountCommands.CMD_SELL_ITEM, shopRev, itemTypeIdx, itemInvID, count, proxy)
            return

    def __sellVehicle_onShopSynced(self, vehInvID, flags, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_SELL_VEHICLE, shopRev, vehInvID, flags, proxy)
            return

    def __respecTman_onShopSynced(self, tmanInvID, vehTypeCompDescr, tmanCostTypeIdx, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt4(AccountCommands.CMD_TMAN_RESPEC, shopRev, tmanInvID, tmanCostTypeIdx, vehTypeCompDescr, proxy)
            return

    def __replacePassport_onShopSynced(self, tmanInvID, isFemale, firstNameID, lastNameID, iconID, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            arr = [
             shopRev, tmanInvID]
            if isFemale is None:
                arr.append(-1)
            elif isFemale:
                arr.append(1)
            else:
                arr.append(0)
            arr.append(firstNameID if firstNameID is not None else -1)
            arr.append(lastNameID if lastNameID is not None else -1)
            arr.append(iconID if iconID is not None else -1)
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdIntArr(AccountCommands.CMD_TMAN_PASSPORT, arr, proxy)
            return

    def __changeVehCamouflage_onShopSynced(self, vehInvID, camouflageID, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_VEH_CAMOUFLAGE, shopRev, vehInvID, camouflageID, proxy)
            return

    def __changeVehHorn_onShopSynced(self, vehInvID, hornID, callback, resultID, shopRev):
        if resultID < 0:
            if callback is not None:
                callback(resultID)
            return
        else:
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_VEH_HORN, shopRev, vehInvID, hornID, proxy)
            return

    def __synchronizeDicts(self, diff, cache):
        for (key, value) in diff.iteritems():
            if value is None:
                cache.pop(key, None)
            elif isinstance(value, dict):
                self.__synchronizeDicts(value, cache.setdefault(key, {}))
            else:
                cache[key] = value

        return


return
