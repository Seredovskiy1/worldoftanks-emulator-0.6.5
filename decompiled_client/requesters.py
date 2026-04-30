# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/gui/Scaleform/utils/requesters.py
# Compiled at: 2011-05-26 15:49:26
import BigWorld
from adisp import async, process
from debug_utils import LOG_ERROR, LOG_DEBUG, LOG_NOTE
from gui_items import InventoryItem, ShopItem, InventoryVehicle, InventoryTankman, VehicleItem
import nations
from items import ITEM_TYPE_NAMES, ITEM_TYPE_INDICES
from Account import PlayerAccount
import dossiers, constants
g_suitableModules = None
g_suitableVehicles = None
_ARTEFACTS_ITEMS = (
 ITEM_TYPE_INDICES['optionalDevice'], ITEM_TYPE_INDICES['equipment'])

class Parser(object):

    @staticmethod
    def parseVehicles(data):
        return data

    @staticmethod
    def parseModules(data, type):
        return data

    @staticmethod
    def getParser(itemTypeID):
        if itemTypeID == 1:
            return Parser.parseVehicles
        return (lambda data: Parser.parseModules(data, itemTypeID))


class InventoryParser(Parser):

    @staticmethod
    def parseVehicles(data):
        vehicles = []
        for (id, vehCompDescr) in data['compDescr'].items():
            descriptor = vehCompDescr
            ammoLayout = dict(data['shellsLayout'].get(id, {}))
            shells = list(data['shells'].get(id, []))
            crew = list(data['crew'].get(id, []))
            (repairCost, health) = data['repair'].get(id, (0, 0))
            equipmentsLayout = data['eqsLayout'].get(id, [0, 0, 0])
            equipments = data['eqs'].get(id, [0, 0, 0])
            if not equipments:
                equipments = [
                 0, 0, 0]
            settings = data['settings'].get(id, 0)
            lock = data['lock'].get(id, 0)
            vehicles.append(InventoryVehicle(compactDescr=descriptor, id=id, crew=crew, shells=shells, ammoLayout=ammoLayout, repairCost=repairCost, health=health, lock=lock, equipments=equipments, equipmentsLayout=equipmentsLayout, settings=settings))

        return vehicles

    @staticmethod
    def parseTankmen(data):
        tankmen = []
        for (id, compDescr) in data['compDescr'].items():
            descriptor = compDescr
            vehicleID = data['vehicle'].get(id, -1)
            tankmen.append(InventoryTankman(compactDescr=descriptor, id=id, vehicleID=vehicleID))

        return tankmen

    @staticmethod
    def parseModules(data, itemTypeID):
        modules = []
        for (descriptor, count) in data.items():
            modules.append(InventoryItem(itemTypeName=ITEM_TYPE_NAMES[itemTypeID], compactDescr=descriptor, count=count))

        return modules

    @staticmethod
    def getParser(itemTypeID):
        if itemTypeID == 1:
            return InventoryParser.parseVehicles
        if itemTypeID == 8:
            return InventoryParser.parseTankmen
        return (lambda data: InventoryParser.parseModules(data, itemTypeID))


class ShopParser(Parser):

    @staticmethod
    def parseVehicles(data, nationId):
        vehicles = []
        for (compactDescr, price) in data[0].items():
            vehicles.append(ShopItem(itemTypeName=ITEM_TYPE_NAMES[1], compactDescr=compactDescr, priceOrder=price, nation=nationId, hidden=compactDescr in data[1]))

        return vehicles

    @staticmethod
    def parseModules(data, itemTypeID, nationId):
        modules = []
        for (compactDescr, price) in data[0].items():
            modules.append(ShopItem(itemTypeName=ITEM_TYPE_NAMES[itemTypeID], compactDescr=compactDescr, priceOrder=price, nation=nationId, hidden=compactDescr in data[1]))

        return modules

    @staticmethod
    def getParser(itemTypeID):
        if itemTypeID == 1:
            return ShopParser.parseVehicles
        return (lambda data, nationId: ShopParser.parseModules(data, itemTypeID, nationId))


class Requester(object):
    """
        Async requester of gui_items @see: helpers.Scaleform.utils.gui_items
        @param itemTypeName: item type to request @see: items.ITEM_TYPE_NAMES

        Example of usage:
        #Dont forget annotate function @process
        @process
        def updateGuns():
                #make request to inventory
                inventoryGuns = yield Requester('vehicleGun').getFromInventory()
                #continue after request complete
                #do somthing with guns list [InventoryItem, InventoryItem, ...]

                #make request to shop
                guns = yield Requester('vehicleGun').getFromShop()
                #continue after request complete
                #do somthing with guns list [ShopItem, ShopItem, ...]
        """
    PARSERS = {'inventory': InventoryParser, 
       'shop': ShopParser}

    def __init__(self, itemTypeName):
        self._itemTypeId = ITEM_TYPE_INDICES[itemTypeName]
        self._callback = None
        self._requestsCount = 0
        self._responsesCount = 0
        self._response = []
        return

    @async
    def getAllPossible(self, callback):
        """
                Make request to inventory and shop
                return InventoryItems and ShopItems

                Example of usage:
                @process
                def updateGuns():
                        guns = yield Requester('vehicleGun').getAllPossible()
                        #continue after request complete
                        #do somthing with guns list [InventoryItem, InventoryItem, ShopItem, ...]
                """
        self._callback = callback
        self._requestsCount = count(nations.INDICES) + 1
        self._requestInventory()
        for nationId in nations.INDICES.values():
            self._requestShop(nationId)

        return

    @async
    def getFromInventory(self, callback):
        """
                Make request to inventory

                Example of usage:
                @process
                def updateGuns():
                        guns = yield Requester('vehicleGun').getFromInventory()
                        #continue after request complete
                        #do somthing with guns list [InventoryItem, InventoryItem, ...]
                """
        self._callback = callback
        self._requestsCount = 1
        self._requestInventory()
        return

    @async
    def getFromShop(self, callback, nation=None):
        """
                Make request to shop

                Example of usage:
                @process
                def updateGuns():
                        guns = yield Requester('vehicleGun').getFromShop()
                        #continue after request complete
                        #do somthing with guns list [ShopItem, ShopItem, ...]
                """
        self._callback = callback
        if self._itemTypeId in _ARTEFACTS_ITEMS:
            self._requestsCount = 1
            self._requestShop(nations.NONE_INDEX)
        elif nation is not None:
            self._requestsCount = 1
            self._requestShop(nation)
        else:
            self._requestsCount = len(nations.INDICES)
            for nationId in nations.INDICES.values():
                self._requestShop(nationId)

            return

    def _requestInventory(self):
        assert hasattr(BigWorld.player(), 'inventory'), 'Request from inventory is not possible'
        BigWorld.player().inventory.getItems(self._itemTypeId, self.__parseInventoryResponse)
        return

    def __parseInventoryResponse(self, responseCode, data):
        listData = []
        if responseCode >= 0:
            listData = Requester.PARSERS['inventory'].getParser(self._itemTypeId)(data)
        else:
            LOG_ERROR('Server return error for inventory getItems request: responseCode=%s, itemTypeId=%s.' % (responseCode, self._itemTypeId))
        self._collectResponse(listData, 'inventory')
        return

    def _requestShop(self, nationId):
        assert hasattr(BigWorld.player(), 'shop'), 'Request from shop is not possible'
        BigWorld.player().shop.getItems(self._itemTypeId, nationId, (lambda responseCode, data, shopRev: self.__parseShopResponse(responseCode, data, nationId)))
        return

    def __parseShopResponse(self, responseCode, data, nationId):
        listData = []
        if responseCode >= 0:
            listData = Requester.PARSERS['shop'].getParser(self._itemTypeId)(data, nationId)
        else:
            LOG_ERROR('Server return error for shop getItems request: responseCode=%s, itemTypeId=%s, nationId=%s, data=%s.' % (responseCode, self._itemTypeId, nationId, data))
        self._collectResponse(listData, 'shop')
        return

    def _collectResponse(self, response, requestType):
        self._responsesCount += 1
        self._response.extend(response)
        if self._responsesCount == self._requestsCount:
            if self._callback is not None:
                self._callback(self._response)
        return


def _getComponentsByType(vehicle, itemTypeId, turretPID=0):
    """
        Return list suitable modules for vehicle foloving structure:
        {
                compactDescriptor: (isCurrent, isDefault),
                ...
        }
        """
    vd = vehicle.descriptor
    components = {}
    descriptorsList = []
    current = None
    if itemTypeId == ITEM_TYPE_INDICES['vehicleChassis']:
        current = vd.chassis
        descriptorsList = vd.type.chassis
    if itemTypeId == ITEM_TYPE_INDICES['vehicleEngine']:
        current = vd.engine
        descriptorsList = vd.type.engines
    if itemTypeId == ITEM_TYPE_INDICES['vehicleRadio']:
        current = vd.radio
        descriptorsList = vd.type.radios
    if itemTypeId == ITEM_TYPE_INDICES['vehicleFuelTank']:
        current = vd.fuelTank
        descriptorsList = vd.type.fuelTanks
    if itemTypeId == ITEM_TYPE_INDICES['vehicleTurret']:
        current = vd.turret
        descriptorsList = vd.type.turrets[turretPID]
    if itemTypeId == ITEM_TYPE_INDICES['optionalDevice']:
        descriptorsList = vd.optionalDevices
    if itemTypeId == ITEM_TYPE_INDICES['equipment']:
        descriptorsList = vehicle.equipments
    if itemTypeId == ITEM_TYPE_INDICES['vehicleGun']:
        current = vd.gun
        for gun in vd.turret['guns']:
            descriptorsList.append(gun)

        for turret in vd.type.turrets[turretPID]:
            if turret is not vd.turret:
                for gun in turret['guns']:
                    descriptorsList.append(gun)

    if itemTypeId == ITEM_TYPE_INDICES['optionalDevice']:
        for (index, item) in enumerate(descriptorsList):
            if item:
                components[item['compactDescr']] = index

    elif itemTypeId == ITEM_TYPE_INDICES['equipment']:
        for (index, item) in enumerate(descriptorsList):
            if item:
                components[item] = index

    else:
        for item in descriptorsList:
            key = item['compactDescr']
            if not components.has_key(key):
                components[key] = item is current

        return components


class AvailableItemsRequester(Requester):

    def __init__(self, vehicle, itemTypeName):
        assert vehicle is not None
        Requester.__init__(self, itemTypeName)
        self._vehicle = vehicle
        return

    @async
    def request(self, callback):
        self._callback = callback
        self._requestsCount = 2
        self._requestInventory()
        if self._itemTypeId in _ARTEFACTS_ITEMS:
            self._requestShop(nations.NONE_INDEX)
        else:
            (nationId, vehicleTypeId) = self._vehicle.descriptor.type.id
            self._requestShop(nationId)
        return

    def _collectResponse(self, response, requestType):
        self._responsesCount += 1
        if requestType == 'shop':
            for item1 in response:
                isIn = False
                for item2 in self._response:
                    if item1 == item2:
                        item2.priceOrder = item1.priceOrder
                        isIn = True
                        break

                if not isIn:
                    self._response.append(item1)

        else:
            for item1 in response:
                for item2 in self._response:
                    if item1 == item2:
                        item1.priceOrder = item2.priceOrder
                        self._response.remove(item2)
                        break

                self._response.append(item1)

            if self._responsesCount == self._requestsCount:
                values = []
                components = _getComponentsByType(self._vehicle, self._itemTypeId)
                descriptors = components.keys()
                for item in self._response:
                    if self._itemTypeId not in _ARTEFACTS_ITEMS:
                        if item.compactDescr not in descriptors:
                            continue
                    elif not item.descriptor.checkCompatibilityWithVehicle(self._vehicle.descriptor)[0]:
                        continue
                    isCurrentOrIndex = components.get(item.compactDescr, False)
                    isCurrent = isCurrentOrIndex if isinstance(isCurrentOrIndex, bool) else True
                    if isCurrent and isinstance(item, ShopItem):
                        item = InventoryItem(itemTypeName=item.itemTypeName, compactDescr=item.compactDescr, priceOrder=item.priceOrder, count=1)
                    item.isCurrent = isCurrent
                    if not isinstance(isCurrentOrIndex, bool):
                        item.index = isCurrentOrIndex
                    values.append(item)

                if self._callback is not None:
                    self._callback(values)
            return


class CurrentVehicleRequester(object):

    def __init__(self):
        self.__callback = None
        return

    @async
    def get(self, callback):
        assert hasattr(BigWorld.player(), 'stats'), 'Request from stats is not possible'
        self.__callback = callback
        BigWorld.player().stats.get('currentVehInvID', self.__getResponse)
        return

    @async
    def set(self, vehicleId, callback):
        assert hasattr(BigWorld.player(), 'stats'), 'Request from stats is not possible'
        self.__callback = callback
        BigWorld.player().stats.setCurrentVehicle(vehicleId, (lambda code: self.__setResponse(code, vehicleId)))
        return

    @async
    def getMyVehicleInfo(self, vehicleId, callback):
        assert hasattr(BigWorld.player(), 'inventory'), 'Request from inventory is not possible'
        self.__callback = callback
        BigWorld.player().inventory.getVehicleInfo(vehicleId, (lambda code, data: self.__parseVehicleResponse(code, data, vehicleId)))
        return

    def __parseVehicleResponse(self, responseCode, data, id):
        vehicleData = None
        if responseCode >= 0:
            vehicleData = InventoryVehicle(data[0], id, (0, 0), data[1], data[2], data[4], data[5])
        else:
            LOG_ERROR('Server return error for inventory getVehicleInfo request: responseCode=%s, itemTypeId=%s.' % (responseCode, self._itemTypeId))
        if self.__callback:
            self.__callback(vehicleData)
        return

    def __getResponse(self, responseCode, vehicleId):
        if responseCode < 0:
            LOG_ERROR('Server return error for statr get currentVehicleInvID request: responseCode=%s, vehicleId=%s.' % (responseCode, vehicleId))
        if self.__callback:
            self.__callback(vehicleId)
        return

    def __setResponse(self, responseCode, vehicleId):
        if responseCode < 0:
            LOG_ERROR('Server return error for statr get currentVehicleInvID request: responseCode=%s, vehicleId=%s.' % (responseCode, vehicleId))
        if self.__callback:
            self.__callback(responseCode)
        return


def responseIfNotAccount(*dargs, **dkwargs):

    def decorate(fn):

        def checkAccount(*fargs, **fkwargs):
            if not isinstance(BigWorld.player(), PlayerAccount):
                LOG_NOTE('Server call "StatsRequester.%s" canceled? player is not account.' % fn.func_name)
                returnFurnc = dkwargs.get('func', None)
                if returnFurnc:
                    returnArgs = dkwargs.get('args', None)
                    if returnArgs:
                        return fkwargs['callback'](returnFurnc(returnArgs))
                    return fkwargs['callback'](returnFurnc())
                return fkwargs['callback'](*dargs, **dkwargs)
            else:
                fargs[0].setCallback(fkwargs['callback'])
                return fn(*fargs, **fkwargs)

        return checkAccount

    return decorate


class StatsRequester(object):

    def __init__(self):
        self.__callback = None
        return

    def setCallback(self, callback):
        self.__callback = callback
        return

    @async
    @responseIfNotAccount(set())
    def getDoubleXPVehicles(self, callback):
        BigWorld.player().stats.get('doubleXPVehs', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(func=dossiers.getAccountDossierDescr, args=('', ))
    def getAccountDossier(self, callback):
        BigWorld.player().stats.get('dossier', self.__accountDossierResponse)
        return

    @async
    @responseIfNotAccount(func=dossiers.getVehicleDossierDescr, args=('', ))
    def getVehicleDossier(self, vehTypeCompDescr, callback):
        BigWorld.player().dossierCache.get(constants.DOSSIER_TYPE.VEHICLE, vehTypeCompDescr, self.__vehicleDossierResponse)
        return

    @async
    @responseIfNotAccount(func=dossiers.getTankmanDossierDescr, args=('', ))
    def getTankmanDossier(self, tankmanID, callback):
        BigWorld.player().dossierCache.get(constants.DOSSIER_TYPE.TANKMAN, tankmanID, self.__tankmanDossierResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getCredits(self, callback):
        BigWorld.player().stats.get('credits', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def isTeamKiller(self, callback):
        BigWorld.player().stats.get('tkillIsSuspected', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def getRestrictions(self, callback):
        BigWorld.player().stats.get('restrictions', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getGold(self, callback):
        BigWorld.player().stats.get('gold', self.__valueResponse)
        return

    @async
    @responseIfNotAccount({})
    def getVehicleTypeExperiences(self, callback):
        BigWorld.player().stats.get('vehTypeXP', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getFreeExperience(self, callback):
        BigWorld.player().stats.get('freeXP', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def exchange(self, gold, callback):
        BigWorld.player().stats.exchange(gold, self.__response)
        return

    @async
    @responseIfNotAccount(False)
    def convertToFreeXP(self, xp, vehTypeDescr, callback):
        BigWorld.player().stats.convertToFreeXP([vehTypeDescr], xp, self.__response)
        return

    @async
    @responseIfNotAccount(False)
    def convertVehiclesXP(self, xp, vehTypeDescrs, callback):
        BigWorld.player().stats.convertToFreeXP(vehTypeDescrs, xp, self.__response)
        return

    @async
    @responseIfNotAccount(set())
    def getUnlocks(self, callback):
        BigWorld.player().stats.get('unlocks', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(set())
    def getEliteVehicles(self, callback):
        BigWorld.player().stats.get('eliteVehicles', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def upgradeToPremium(self, days, callback):
        BigWorld.player().stats.upgradeToPremium(days, self.__response)
        return

    @async
    @responseIfNotAccount(False)
    def buySlot(self, callback):
        BigWorld.player().stats.buySlot(self.__response)
        return

    @async
    @responseIfNotAccount(0)
    def getSlotsCount(self, callback):
        BigWorld.player().stats.get('slots', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getTankmenBerthsCount(self, callback):
        BigWorld.player().stats.get('berths', self.__valueResponse)
        return

    @async
    @responseIfNotAccount([0, [0]])
    def getSlotsPrices(self, callback):
        BigWorld.player().shop.getSlotsPrices(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getPaidRemovalCost(self, callback):
        BigWorld.player().shop.getPaidRemovalCost(self.__valueResponse)
        return

    @async
    @responseIfNotAccount([0, 1, [0]])
    def getBerthsPrices(self, callback):
        BigWorld.player().shop.getBerthsPrices(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def buyBerths(self, callback):
        BigWorld.player().stats.buyBerths(self.__response)
        return

    @async
    @responseIfNotAccount(tuple())
    def getTankmanCost(self, callback):
        BigWorld.player().shop.getTankmanCost(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getPassportChangeCost(self, callback):
        BigWorld.player().shop.getPassportChangeCost(self.__valueResponse)
        return

    @async
    @responseIfNotAccount((0, 0))
    def getShellPrice(self, nationIdx, shellCompactDescr, callback):
        BigWorld.player().shop.getPrice(ITEM_TYPE_INDICES['shell'], nationIdx, shellCompactDescr, self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def getSellPriceModifiers(self, callback):
        BigWorld.player().shop.getSellPriceModifiers(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(1)
    def getExchangeRate(self, callback):
        BigWorld.player().shop.getExchangeRate(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(None)
    def getFreeXPConversion(self, callback):
        BigWorld.player().shop.getFreeXPConversion(self.__valueResponse)
        return

    @async
    @responseIfNotAccount({})
    def getPremiumCost(self, callback):
        BigWorld.player().shop.getPremiumCost(self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getAccountAttrs(self, callback):
        BigWorld.player().stats.get('attrs', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(0)
    def getPremiumExpiryTime(self, callback):
        BigWorld.player().stats.get('premiumExpiryTime', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(None)
    def getClanInfo(self, callback):
        BigWorld.player().stats.get('clanInfo', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(None)
    def getUserClanInfo(self, userName, callback):
        BigWorld.player().requestPlayerClanInfo(userName, (lambda resultID, clanDBID, clanInfo: self.__valueResponse(resultID, clanInfo)))
        return

    @async
    @responseIfNotAccount(False)
    def hasFinPassword(self, callback):
        BigWorld.player().stats.get('hasFinPassword', self.__valueResponse)
        return

    @async
    @responseIfNotAccount({})
    def getVehiclesPrices(self, vehicles, callback):
        BigWorld.player().shop.getVehiclesSellPrices(vehicles, self.__valueResponse)
        return

    @async
    @responseIfNotAccount(None)
    def getFreeVehicleLeft(self, callback):
        BigWorld.player().stats.get('freeVehiclesLeft', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(None)
    def getFreeTankmanLeft(self, callback):
        BigWorld.player().stats.get('freeTMenLeft', self.__valueResponse)
        return

    @async
    @responseIfNotAccount(False)
    def setEquipments(self, vehInvId, equipments, callback):
        BigWorld.player().inventory.equipEquipments(vehInvId, equipments, self.__response)
        return

    @async
    @responseIfNotAccount({})
    def getTradeFees(self, callback):
        assert hasattr(BigWorld.player(), 'shop'), 'Request from shop is not possible'
        self.__callback = callback
        BigWorld.player().shop.getTradeFees(self.__valueResponse)
        return

    def __accountDossierResponse(self, responseCode, dossierCompDescr=''):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat account dossier request: responseCode=%s' % responseCode)
            return
        if self.__callback:
            import dossiers
            dossierDescr = dossiers.getAccountDossierDescr(dossierCompDescr)
            self.__callback(dossierDescr)
        return

    def __vehicleDossierResponse(self, responseCode, vehTypeDossiers=''):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat account dossier request: responseCode=%s' % responseCode)
            return
        else:
            if self.__callback:
                import dossiers
                if vehTypeDossiers is not None:
                    self.__callback(dossiers.getVehicleDossierDescr(vehTypeDossiers))
                self.__callback(dossiers.getVehicleDossierDescr(''))
            return

    def __tankmanDossierResponse(self, responseCode, tankmanTypeDossiers=''):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat account dossier request: responseCode=%s' % responseCode)
            return
        else:
            if self.__callback:
                if tankmanTypeDossiers is not None:
                    self.__callback(dossiers.getTankmanDossierDescr(tankmanTypeDossiers))
                self.__callback(dossiers.getTankmanDossierDescr(''))
            return

    def _valueResponse(self, responseCode, value=None, revision=0):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat request: responseCode=%s' % responseCode)
        elif self.__callback:
            self.__callback(value)
        return

    def __valueResponse(self, responseCode, value=None, revision=0):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat request: responseCode=%s' % responseCode)
        elif self.__callback:
            self.__callback(value)
        return

    def __response(self, responseCode):
        if responseCode < 0:
            LOG_ERROR('Server return error for stat request: responseCode=%s.' % responseCode)
        if self.__callback:
            self.__callback(responseCode >= 0)
        return


class VehicleItemsRequester(object):

    def __init__(self, vehicles):
        self.__vehicles = vehicles
        return

    def getItems(self, types):
        items = {}
        for v in self.__vehicles:
            for type in types:
                currents = self.__getItemsByType(v, type)
                for current in currents:
                    if current:
                        current = items.setdefault(current, VehicleItem(compactDescr=current))
                        current.count += 1

        return items.values()

    def __getItemsByType(self, v, itemTypeName):
        vd = v.descriptor
        if itemTypeName == 'vehicleChassis':
            return [vd.chassis['compactDescr']]
        if itemTypeName == 'vehicleEngine':
            return [vd.engine['compactDescr']]
        if itemTypeName == 'vehicleRadio':
            return [vd.radio['compactDescr']]
        if itemTypeName == 'vehicleFuelTank':
            return [vd.fuelTank['compactDescr']]
        if itemTypeName == 'vehicleTurret':
            if v.type not in ('AT-SPG', 'SPG'):
                return [vd.turret['compactDescr']]
        if itemTypeName == 'vehicleGun':
            return [vd.gun['compactDescr']]
        if itemTypeName == 'optionalDevice':
            return [_[1] for od in vd.optionalDevices if od]
        if itemTypeName == 'equipment':
            return v.equipments
        return []


return
