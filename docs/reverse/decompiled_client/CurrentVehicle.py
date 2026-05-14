# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/CurrentVehicle.py
# Compiled at: 2011-05-26 15:49:28
import BigWorld
from AccountCommands import LOCK_REASON
from account_helpers.AccountSettings import AccountSettings
from adisp import process, async
from debug_utils import LOG_DEBUG
from PlayerEvents import g_playerEvents
from Event import Event, EventManager
from gui.Scaleform.Waiting import Waiting

class _CurrentVehicle(object):

    def __init__(self):
        self.firstTimeInitialized = False
        self.__eventManager = EventManager()
        self.onChanged = Event(self.__eventManager)
        self.__vehicle = None
        self.__changeCallbackID = None
        return

    def __reset(self):
        self.firstTimeInitialized = False
        self.__vehicle = None
        return

    def cleanup(self):
        self.reset(True)
        self.__eventManager.clear()
        return

    def __setVehicleToServer(self, id):
        AccountSettings.setFavorites('current', id)
        return

    def __repr__(self):
        return 'CurrentVehicle(%s)' % str(self.__vehicle)

    def __getVehicle(self):
        return self.__vehicle

    def __setVehicle(self, newVehicle):
        self.__request(newVehicle.inventoryId)
        return

    def setVehicleById(self, id):
        self.__request(id)
        return

    vehicle = property(__getVehicle, __setVehicle)

    def __getRepairCost(self):
        return self.__vehicle.repairCost

    def __setRepairCost(self, newValue):
        if self.__vehicle.repairCost != newValue:
            self.__vehicle.repairCost = newValue
            self.onChanged()
        return

    repairCost = property(__getRepairCost, __setRepairCost)

    def isBroken(self):
        return self.__vehicle.repairCost > 0

    def setLocked(self, newValue):
        if self.__vehicle.lock != newValue:
            self.__vehicle.lock = newValue
            self.onChanged()
        return

    def isCrewFull(self):
        return self.isPresent() and None not in self.__vehicle.crew and self.__vehicle.crew != []

    def isInBattle(self):
        return self.__vehicle.lock == LOCK_REASON.ON_ARENA

    def isInHangar(self):
        return self.isPresent() and not self.isInBattle()

    def isAwaitingBattle(self):
        return self.__vehicle.lock == LOCK_REASON.IN_QUEUE

    def isLocked(self):
        return self.__vehicle.lock != LOCK_REASON.NONE

    def isAlive(self):
        return self.isPresent() and not self.isBroken() and not self.isLocked()

    def isReadyToFight(self):
        return self.isAlive() and self.isCrewFull()

    def isPresent(self):
        return self.__vehicle is not None

    def getState(self):
        if not self.isInHangar():
            return None
        else:
            if self.__vehicle.modelState != 'damaged':
                return self.__vehicle.modelState
            return 'undamaged'

    def getHangarMessage(self):
        message = '#menu:currentVehicleStatus/'
        if self.vehicle is None:
            message += 'notpresent'
        elif self.isInBattle():
            message += 'inbattle'
        elif self.isLocked():
            message += 'locked'
        elif not self.isCrewFull():
            message += 'crewNotFull'
        else:
            message += self.__vehicle.modelState
        return message

    def reset(self, silent=False):
        self.__reset()
        if not silent:
            self.onChanged()
        return

    @process
    def __request(self, inventoryId):
        Waiting.show('updateCurrentVehicle', True)
        from gui.Scaleform.utils.requesters import Requester
        vehicles = yield Requester('vehicle').getFromInventory()
        old = self.__vehicle
        self.__vehicle = self.__findCurrent(inventoryId, vehicles)
        if self.__vehicle and self.__vehicle != old:
            self.__setVehicleToServer(self.__vehicle.inventoryId)
        if not self.__changeCallbackID:
            self.__changeCallbackID = BigWorld.callback(0.1, self.__changeDone)
        return

    def __changeDone(self):
        self.__changeCallbackID = None
        player = BigWorld.player()
        if player and hasattr(player, 'isPlayer') and player.isPlayer:
            self.onChanged()
        Waiting.hide('updateCurrentVehicle')
        return

    def __findCurrent(self, inventoryId, vehicles):
        for vehicle in vehicles:
            if vehicle.inventoryId == inventoryId:
                return vehicle

        vehicles.sort()
        if len(vehicles):
            return vehicles[0]
        else:
            return

    def update(self):
        if self.firstTimeInitialized:
            self.__request(self.__vehicle.inventoryId if self.__vehicle else None)
        return

    @async
    @process
    def getFromServer(self, callback):
        currentId = AccountSettings.getFavorites('current')
        from gui.Scaleform.utils.requesters import Requester
        vehicles = yield Requester('vehicle').getFromInventory()
        self.__vehicle = self.__findCurrent(currentId, vehicles)
        self.onChanged()
        self.firstTimeInitialized = True
        callback(True)
        return


g_currentVehicle = _CurrentVehicle()
return
