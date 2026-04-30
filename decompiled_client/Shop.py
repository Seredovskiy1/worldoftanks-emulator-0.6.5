# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/account_helpers/Shop.py
# Compiled at: 2011-05-26 15:49:28
import AccountCommands, items, nations
from functools import partial
from math import ceil
from itertools import izip
from items import vehicles, tankmen
from AccountCommands import BUY_VEHICLE_FLAG
from account_shared import AmmoIterator
from SyncController import SyncController
from PlayerEvents import g_playerEvents as events
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

class Shop(object):

    def __init__(self):
        self.__account = None
        self.__syncController = None
        self.__cache = {}
        self.__ignore = True
        self.__isSynchronizing = False
        self.__syncID = 0
        self.__isFirstSync = True
        return

    def onAccountBecomePlayer(self):
        self.__ignore = False
        self.__isFirstSync = True
        self.synchronize()
        return

    def onAccountBecomeNonPlayer(self):
        self.__ignore = True
        self.__isSynchronizing = False
        return

    def setAccount(self, account):
        self.__account = account
        if self.__syncController is not None:
            self.__syncController.destroy()
            self.__syncController = None
        if account is not None:
            self.__syncController = SyncController(account, self.__sendSyncRequest, self.__onSyncResponse, self.__onSyncComplete)
        return

    def synchronize(self, serverCacheRev=None):
        if self.__ignore:
            return
        else:
            if self.__getCacheRevision() == serverCacheRev:
                return
            if self.__isSynchronizing:
                return
            self.__isSynchronizing = True
            self.__syncController.request(self.__getNextSyncID(), None)
            return

    def resynchronize(self):
        LOG_MX('resynchronize')
        if self.__ignore:
            return
        else:
            self.__cache.clear()
            self.__isSynchronizing = True
            self.__syncController.request(self.__getNextSyncID(), None)
            return

    def waitForSync(self, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, None)
            return
        else:
            if not self.__isSynchronizing:
                callback(AccountCommands.RES_CACHE, self.__getCacheRevision())
                return
            proxy = lambda resultID, data: callback(resultID, self.__getCacheRevision())
            self.__syncController.request(self.__syncID, proxy)
            return

    def getItems(self, itemTypeIdx, nationIdx, callback):
        proxy = lambda resultID, items, rev: self.__onGetItemsResponse(resultID, itemTypeIdx, nationIdx, callback)
        self.__getValue('items', proxy)
        return

    def getAllItems(self, callback):
        self.__getValue('items', callback)
        return

    def getSellPriceModifiers(self, callback):
        proxy = lambda resultID, data, rev: callback(resultID, self.__getSellPriceModifiersFromCache())
        self.__getValue('sellPriceModif', proxy)
        return

    def getSellPrice(self, buyPrice, sellPriceModifiers):
        (shopRev, exchangeRate, sellPriceModif) = sellPriceModifiers
        assert shopRev == self.__getCacheRevision()
        return int(ceil(sellPriceModif * (buyPrice[0] + buyPrice[1] * exchangeRate)))

    def getPrice(self, itemTypeIdx, nationIdx, itemShopID, callback):
        proxy = lambda resultID, items, rev: self.__onGetPriceResponse(resultID, itemTypeIdx, nationIdx, itemShopID, callback)
        self.__getValue('items', proxy)
        return

    def getVehiclePrice(self, vehCompDescr, callback):
        proxy = lambda resultID, items, rev: self.__onGetVehiclePriceResponse(resultID, vehCompDescr, False, callback)
        self.__getValue('items', proxy)
        return

    def getVehicleSellPrice(self, vehCompDescr, callback):
        proxy = lambda resultID, items, rev: self.__onGetVehiclePriceResponse(resultID, vehCompDescr, True, callback)
        self.__getValue('items', proxy)
        return

    def getVehiclesSellPrices(self, vehCompDescrs, callback):
        proxy = lambda resultID, items, rev: self.__onGetVehiclesSellPriceResponse(resultID, vehCompDescrs, callback)
        self.__getValue('items', proxy)
        return

    def getComponentPrice(self, compDescr, callback):
        proxy = lambda resultID, items, rev: self.__onGetComponentPriceResponse(resultID, compDescr, False, callback)
        self.__getValue('items', proxy)
        return

    def getComponentSellPrice(self, compDescr, callback):
        proxy = lambda resultID, items, rev: self.__onGetComponentPriceResponse(resultID, compDescr, True, callback)
        self.__getValue('items', proxy)
        return

    def getAmmoSellPrice(self, ammo, callback):
        proxy = lambda resultID, items, rev: self.__onGetAmmoSellPriceResponse(resultID, ammo, callback)
        self.__getValue('items', proxy)
        return

    def getSlotsPrices(self, callback):
        self.__getValue('slotsPrices', callback)
        return

    def getNextSlotPrice(self, slots, slotsPrices):
        addSlotNumber = slots - slotsPrices[0]
        if addSlotNumber < 0:
            return 0
        if addSlotNumber < len(slotsPrices[1]):
            return slotsPrices[1][addSlotNumber]
        return slotsPrices[1][-1]

    def getBerthsPrices(self, callback):
        self.__getValue('berthsPrices', callback)
        return

    def getNextBerthPackPrice(self, berths, berthsPrices):
        addPackNumber = (berths - berthsPrices[0]) / berthsPrices[1]
        if addPackNumber < 0:
            return 0
        if addPackNumber < len(berthsPrices[2]):
            return berthsPrices[2][addPackNumber]
        return berthsPrices[2][-1]

    def getExchangeRate(self, callback):
        self.__getValue('exchangeRate', callback)
        return

    def getFreeXPConversion(self, callback):
        self.__getValue('freeXPConversion', callback)
        return

    def getPremiumCost(self, callback):
        self.__getValue('premiumCost', callback)
        return

    def getTradeFees(self, callback):
        self.__getValue('tradeFees', callback)
        return

    def getTankmanCost(self, callback):
        self.__getValue('tankmanCost', callback)
        return

    def getPassportChangeCost(self, callback):
        self.__getValue('passportChangeCost', callback)
        return

    def getPaidRemovalCost(self, callback):
        self.__getValue('paidRemovalCost', callback)
        return

    def getCamouflageCost(self, nationIdx, callback):
        proxy = lambda resultID, allNationsCost, rev: self.__onGetCamouflageCostResponse(resultID, allNationsCost, nationIdx, callback)
        self.__getValue('camouflageCost', proxy)
        return

    def getHornCost(self, callback):
        self.__getValue('hornCost', callback)
        return

    def buy(self, itemTypeIdx, nationIdx, itemShopID, count, callback):
        if self.__ignore:
            return
        else:
            if itemTypeIdx == _VEHICLE:
                self.buyVehicle(nationIdx, itemShopID, False, True, 0, callback)
                return
            count = int(round(count))
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt3(AccountCommands.CMD_BUY_ITEM, self.__getCacheRevision(), itemShopID, count, proxy)
            return

    def buyVehicle(self, nationIdx, innationIdx, buyShells, recruitCrew, tmanCostTypeIdx, callback):
        if self.__ignore:
            return
        else:
            typeCompDescr = vehicles.makeIntCompactDescrByID('vehicle', nationIdx, innationIdx)
            flags = BUY_VEHICLE_FLAG.NONE
            if buyShells:
                flags |= BUY_VEHICLE_FLAG.SHELLS
            if recruitCrew:
                flags |= BUY_VEHICLE_FLAG.CREW
            if callback is not None:
                proxy = lambda requestID, resultID, ext={}: callback(resultID)
            else:
                proxy = None
            self.__account._doCmdInt4(AccountCommands.CMD_BUY_VEHICLE, self.__getCacheRevision(), typeCompDescr, flags, tmanCostTypeIdx, proxy)
            return

    def buyTankman(self, nationIdx, innationIdx, role, tmanCostTypeIdx, callback):
        vehTypeCompDescr = vehicles.makeIntCompactDescrByID('vehicle', nationIdx, innationIdx)
        roleIdx = tankmen.SKILL_INDICES[role]
        if callback is not None:
            proxy = lambda requestID, resultID, ext={}: callback(resultID, ext.get('tmanInvID', None), ext.get('tmanCompDescr', None))
        else:
            proxy = None
        self.__account._doCmdInt4(AccountCommands.CMD_BUY_TMAN, self.__getCacheRevision(), vehTypeCompDescr, roleIdx, tmanCostTypeIdx, proxy)
        return

    def __onSyncResponse(self, syncID, resultID, ext={}):
        if resultID == AccountCommands.RES_NON_PLAYER:
            return
        if syncID != self.__syncID:
            return
        if resultID < 0:
            LOG_ERROR('Shop synchronization failed. Repeating')
            self.resynchronize()
            return
        self.__isSynchronizing = False
        return

    def __onSyncComplete(self, syncID, data):
        if syncID != self.__syncID:
            return
        else:
            if data is None:
                return
            self.__cache = data
            self.__isSynchronizing = False
            if self.__isFirstSync:
                self.__isFirstSync = False
            else:
                events.onShopResync()
            return

    def __onGetItemsResponse(self, resultID, itemTypeIdx, nationIdx, callback):
        if resultID < 0:
            items = None
        else:
            items = self.__cache.get('items', {}).get(nationIdx, {}).get(itemTypeIdx, None)
        if callback is not None:
            callback(resultID, items, self.__getCacheRevision())
        return

    def __onGetValueResponse(self, resultID, key, callback):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None, self.__getCacheRevision())
            return
        else:
            if self.__isSynchronizing:
                self.__getValue(key, callback)
                return
            value = self.__cache.get(key, None)
            if callback is not None:
                callback(resultID, value, self.__getCacheRevision())
            return

    def __onGetPriceResponse(self, resultID, itemTypeIdx, nationIdx, itemShopID, callback):
        if resultID < 0:
            price = None
        else:
            price = self.__getPriceFromCache(itemTypeIdx, nationIdx, itemShopID)
        if callback is not None:
            callback(resultID, price, self.__getCacheRevision())
        return

    def __onGetVehiclePriceResponse(self, resultID, vehCompDescr, isSellPrice, callback):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None, self.__getCacheRevision())
            return
        else:
            price = self.__getVehiclePriceFromCache(vehCompDescr, None)
            if isSellPrice and price is not None:
                price = self.getSellPrice(price, self.__getSellPriceModifiersFromCache())
            if callback is not None:
                callback(resultID, price, self.__getCacheRevision())
            return

    def __onGetVehiclesSellPriceResponse(self, resultID, vehCompDescrs, callback):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None, self.__getCacheRevision())
            return
        else:
            prices = []
            for vehCompDescr in vehCompDescrs:
                price = self.__getVehiclePriceFromCache(vehCompDescr, None)
                if price is None:
                    prices = None
                    break
                prices.append(self.getSellPrice(price, self.__getSellPriceModifiersFromCache()))

            if callback is not None:
                callback(resultID, prices, self.__getCacheRevision())
            return

    def __onGetComponentPriceResponse(self, resultID, compDescr, isSellPrice, callback):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None, self.__getCacheRevision())
            return
        else:
            (itemTypeIdx, nationIdx, innationIdx) = vehicles.parseIntCompactDescr(compDescr)
            price = self.__getPriceFromCache(itemTypeIdx, nationIdx, compDescr)
            if isSellPrice:
                price = self.getSellPrice(price, self.__getSellPriceModifiersFromCache())
            if callback is not None:
                callback(resultID, price, self.__getCacheRevision())
            return

    def __onGetAmmoSellPriceResponse(self, resultID, ammo, callback):
        if resultID < 0:
            if callback is not None:
                callback(resultID, None, self.__getCacheRevision())
            return
        else:
            price = 0
            for (shellCompDescr, count) in AmmoIterator(ammo):
                if count == 0:
                    continue
                (_, nationIdx, innationIdx) = vehicles.parseIntCompactDescr(shellCompDescr)
                shellPrice = self.__getPriceFromCache(_SHELL, nationIdx, shellCompDescr)
                shellSellPrice = self.getSellPrice(shellPrice, self.__getSellPriceModifiersFromCache())
                price += shellSellPrice * count

            if callback is not None:
                callback(resultID, price, self.__getCacheRevision())
            return

    def __onGetCamouflageCostResponse(self, resultID, allNationsCost, nationIdx, callback):
        if callback is None:
            return
        else:
            if resultID < 0 or allNationsCost is None:
                callback(resultID, None, self.__getCacheRevision())
            else:
                callback(resultID, allNationsCost.get(nationIdx, {}), self.__getCacheRevision())
            return

    def __getNextSyncID(self):
        self.__syncID += 1
        if self.__syncID > 30000:
            self.__syncID = 1
        return self.__syncID

    def __sendSyncRequest(self, id, proxy):
        if self.__ignore:
            return
        clientRev = self.__getCacheRevision()
        self.__account._doCmdInt3(AccountCommands.CMD_SYNC_SHOP, clientRev, 0, 0, proxy)
        return

    def __getCacheRevision(self):
        return self.__cache.get('rev', 0)

    def __getPriceFromCache(self, itemTypeIdx, nationIdx, itemShopID, default=(0, 0)):
        return self.__cache.get('items', {}).get(nationIdx, {}).get(itemTypeIdx, ({}, set()))[0].get(itemShopID, default)

    def __getVehiclePriceFromCache(self, vehCompDescr, default=(0, 0)):
        (nationIdx, innationIdx) = vehicles.parseVehicleCompactDescr(vehCompDescr)
        price = self.__getPriceFromCache(_VEHICLE, nationIdx, innationIdx, None)
        if price is None:
            return default
        else:
            vehDescr = vehicles.VehicleDescr(compactDescr=vehCompDescr)
            devices = vehDescr.getDevices()
            for (defCompDescr, instCompDescr) in izip(devices[0], devices[1]):
                if defCompDescr == instCompDescr:
                    continue
                (itemTypeIdx, nationIdx, innationIdx) = vehicles.parseIntCompactDescr(defCompDescr)
                compPrice = self.__getPriceFromCache(itemTypeIdx, nationIdx, defCompDescr, None)
                if compPrice is None:
                    return default
                price = _subtractPrices(price, compPrice)
                (itemTypeIdx, nationIdx, innationIdx) = vehicles.parseIntCompactDescr(instCompDescr)
                compPrice = self.__getPriceFromCache(itemTypeIdx, nationIdx, instCompDescr, None)
                if compPrice is None:
                    return default
                price = _summPrices(price, compPrice)

            for optDevCompDescr in devices[2]:
                (itemTypeIdx, nationIdx, innationIdx) = vehicles.parseIntCompactDescr(optDevCompDescr)
                compPrice = self.__getPriceFromCache(itemTypeIdx, nationIdx, optDevCompDescr, None)
                if compPrice is None:
                    return default
                price = _summPrices(price, compPrice)

            return price

    def __getSellPriceModifiersFromCache(self):
        return (
         self.__getCacheRevision(), self.__cache.get('exchangeRate', 0),
         self.__cache.get('sellPriceModif', 0))

    def __getValue(self, key, callback):
        if self.__ignore:
            if callback is not None:
                callback(AccountCommands.RES_NON_PLAYER, None, self.__getCacheRevision())
            return
        else:
            if not self.__isSynchronizing:
                self.__onGetValueResponse(AccountCommands.RES_CACHE, key, callback)
                return
            proxy = lambda resultID, data: self.__onGetValueResponse(resultID, key, callback)
            self.__syncController.request(self.__syncID, proxy)
            return


def _summPrices(price1, price2):
    return (
     price1[0] + price2[0], price1[1] + price2[1])


def _subtractPrices(price1, price2):
    return (
     price1[0] - price2[0], price1[1] - price2[1])


return
