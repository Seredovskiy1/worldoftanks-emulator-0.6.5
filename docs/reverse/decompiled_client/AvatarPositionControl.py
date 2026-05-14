# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/AvatarPositionControl.py
# Compiled at: 2011-05-26 15:49:28
import BigWorld, constants, weakref
from debug_utils import *
import time

class AvatarPositionControl:
    FOLLOW_CAMERA_MAX_DEVIATION = 10.0

    def __init__(self, avatar):
        self.__avatar = weakref.proxy(avatar)
        self.__bFollowCamera = False
        return

    def destroy(self):
        self.__avatar = None
        return

    def bindToVehicle(self, bValue=True, vehicleID=None):
        if bValue:
            if vehicleID is None:
                vehicleID = self.__avatar.playerVehicleID
            self.__doBind(vehicleID)
        else:
            self.__doUnbind()
        return

    def followCamera(self, bValue=True):
        self.__bFollowCamera = bValue
        if bValue:
            self.onFollowCameraTick()
        return

    def moveTo(self, pos):
        self.__avatar.cell.moveTo(pos)
        return

    def getFollowCamera(self):
        return self.__bFollowCamera

    def onFollowCameraTick(self):
        if not self.__bFollowCamera:
            return
        else:
            cam = BigWorld.camera()
            if cam is None:
                return
            if BigWorld.camera().position.flatDistTo(self.__avatar.position) >= self.FOLLOW_CAMERA_MAX_DEVIATION:
                self.moveTo(BigWorld.camera().position)
            return

    def __doBind(self, vehicleID):
        pos = self.__avatar.arena.positions.get(vehicleID)
        if pos is None and vehicleID == self.__avatar.playerVehicleID:
            pos = self.__avatar.getOwnVehiclePosition()
        if pos is None:
            pos = self.__avatar.position
        self.__avatar.cell.bindToVehicle(vehicleID, pos)
        return

    def __doUnbind(self):
        self.__avatar.cell.bindToVehicle(0, self.__avatar.position)
        return


return
