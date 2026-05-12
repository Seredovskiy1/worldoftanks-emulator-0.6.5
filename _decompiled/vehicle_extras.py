# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/vehicle_extras.py
# Compiled at: 2011-05-26 15:49:28
import BigWorld, random, items, constants
from Vibroeffects.Controllers.ShootingController import ShootingController
from debug_utils import *
from helpers import i18n
from helpers.EntityExtra import EntityExtra

def reload():
    modNames = (
     reload.__module__,)
    from sys import modules
    import __builtin__
    for m in modNames:
        __builtin__.reload(modules[m])

    print 'vehicle_extras reloaded'
    return


class NoneExtra(EntityExtra):

    def _start(self, data, args):
        debug_utils.LOG_CODEPOINT_WARNING()
        self.stop(data)
        return


import Math
from functools import partial
from helpers.EffectsList import EffectsListPlayer

class ShowShooting(EntityExtra):

    def _start(self, data, args):
        vehicle = data['entity']
        gunDescr = vehicle.typeDescriptor.gun
        (stages, effects, _) = gunDescr['effects']
        data['_effectsListPlayer'] = EffectsListPlayer(effects, stages)
        data['_burst'] = gunDescr['burst']
        data['_gunModel'] = vehicle.appearance.modelsDesc['gun']['model']
        if vehicle.isPlayer:
            maxShots = max(1, BigWorld.player().getCurrentShots()[0])
            (burstCount, burstInterval) = data['_burst']
            if burstCount > maxShots:
                data['_burst'] = (
                 maxShots, burstInterval)
            BigWorld.addAlwaysUpdateModel(data['_gunModel'])
        self.__doShot(data)
        return

    def _cleanup(self, data):
        data['_effectsListPlayer'].stop()
        timerID = data.get('_timerID')
        if timerID is not None:
            BigWorld.cancelCallback(timerID)
            data['_timerID'] = None
        if data['entity'].isPlayer:
            BigWorld.delAlwaysUpdateModel(data['_gunModel'])
        return

    def __doShot(self, data):
        data['_timerID'] = None
        try:
            vehicle = data['entity']
            if not vehicle.isAlive():
                self.stop(data)
                return
            (burstCount, burstInterval) = data['_burst']
            gunModel = data['_gunModel']
            effPlayer = data['_effectsListPlayer']
            effPlayer.stop()
            if burstCount == 1:
                effPlayer.play(gunModel, None, partial(self.stop, data))
                if data['entity'].isPlayer and burstInterval > 0.0:
                    data['_timerID'] = BigWorld.callback(0.5, partial(self.__notifyOnCompletionOfBurst, data))
            else:
                data['_burst'] = (
                 burstCount - 1, burstInterval)
                data['_timerID'] = BigWorld.callback(burstInterval, partial(self.__doShot, data))
                effPlayer.play(gunModel)
            appearance = vehicle.appearance
            appearance.gunRecoil.recoil()
            appearance.receiveShotImpulse(Math.Matrix(gunModel.matrix).applyVector(Math.Vector3(0, 0, -1)), vehicle.typeDescriptor.gun['impulse'])
            appearance.executeShootingVibrations(vehicle.typeDescriptor.shot['shell']['caliber'])
        except Exception:
            LOG_CURRENT_EXCEPTION()
            self.stop(data)

        return

    def __notifyOnCompletionOfBurst(self, data):
        data['_timerID'] = None
        BigWorld.player().playShotResultNotification(None, None)
        return


class DamageMarker(EntityExtra):

    def _readConfig(self, dataSection, containerName):
        self.deviceUserString = dataSection.readString('deviceUserString')
        if not self.deviceUserString:
            self._raiseWrongConfig('deviceUserString', containerName)
        self.deviceUserString = i18n.makeString(self.deviceUserString)
        soundSection = dataSection['sounds']
        self.sounds = {}
        for state in ('critical', 'destroyed', 'functional', 'fixed'):
            sound = soundSection.readString(state)
            if sound:
                self.sounds[state] = sound

        return


class TrackHealth(DamageMarker):

    def _readConfig(self, dataSection, containerName):
        DamageMarker._readConfig(self, dataSection, containerName)
        self.__isLeft = dataSection.readBool('isLeft')
        return

    def _start(self, data, args):
        data['entity'].appearance.addCrashedTrack(self.__isLeft)
        return

    def _cleanup(self, data):
        data['entity'].appearance.delCrashedTrack(self.__isLeft)
        return


class Fire(EntityExtra):

    def _readConfig(self, dataSection, containerName):
        self.sounds = {}
        startSound = dataSection.readString('sounds/fireStarted')
        if startSound:
            self.sounds['critical'] = startSound
            self.sounds['destroyed'] = startSound
        else:
            self._raiseWrongConfig('sounds/fireStarted', containerName)
        stopSound = dataSection.readString('sounds/fireStopped')
        if stopSound:
            self.sounds['fixed'] = stopSound
        else:
            self._raiseWrongConfig('sounds/fireStopped', containerName)
        return

    def _start(self, data, args):
        data['_isStarted'] = False
        vehicle = data['entity']
        (stages, effects, _) = random.choice(vehicle.typeDescriptor.type.effects['flaming'])
        if len(stages) != 2 or stages[0][0] != 'fire' or stages[1][0] != 'noEmission':
            LOG_ERROR("Wrong stages in vehicle flaming effect. Should be 'fire' and 'noEmission'.", vehicle.typeDescriptor.name)
            self.stop(data)
            return
        data['_noEmissionTime'] = stages[1][1]
        data['_effects'] = effects
        effects.attachTo(vehicle.appearance.modelsDesc['hull']['model'], data, 'fire')
        data['_isStarted'] = True
        vehicle.appearance.switchFireVibrations(True)
        return

    def _cleanup(self, data):
        if not data['_isStarted']:
            return
        vehicle = data['entity']
        effects = data['_effects']
        vehicle.appearance.switchFireVibrations(False)
        if vehicle.health <= 0:
            effects.detachAllFrom(data)
            return
        effects.detachFrom(data, 'fire')
        effects.attachTo(vehicle.appearance.modelsDesc['hull']['model'], data, 'noEmission')
        BigWorld.callback(data['_noEmissionTime'], partial(self.__stop, data))
        return

    def __stop(self, data):
        data['_effects'].detachAllFrom(data)
        return


return
