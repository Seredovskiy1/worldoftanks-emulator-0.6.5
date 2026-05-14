# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/Vehicle.py
# Compiled at: 2011-05-26 15:49:28
import BigWorld, Math, weakref, random
from itertools import izip
from debug_utils import *
import constants
from constants import SHOT_RESULT
from items import vehicles
import VehicleAppearance
from gui.WindowsManager import g_windowsManager
import AreaDestructibles, DestructiblesCache, math

class Vehicle(BigWorld.Entity):
    hornMode = property((lambda self: self.__hornMode))

    def __init__(self):
        self.proxy = weakref.proxy(self)
        self.extras = {}
        self.typeDescriptor = None
        self.appearance = None
        self.isPlayer = False
        self.isStarted = False
        self.__prereqs = None
        self.__hornSounds = (None, )
        self.__hornMode = ''
        self.__stopHornSoundCallback = None
        return

    def reload(self):
        wasStarted = self.isStarted
        if self.isStarted:
            self.stopVisual()
        vehicles.reload()
        self.typeDescriptor = vehicles.VehicleDescr(compactDescr=self.publicInfo.compDescr)
        if wasStarted:
            self.appearance = VehicleAppearance.VehicleAppearance()
            self.appearance.prerequisites(self)
            self.startVisual()
        return

    def prerequisites(self):
        if self.typeDescriptor is not None:
            return ()
        else:
            prereqs = []
            descr = vehicles.VehicleDescr(compactDescr=self.publicInfo.compDescr)
            self.typeDescriptor = descr
            prereqs += descr.prerequisites()
            for hitTester in descr.getHitTesters():
                if hitTester.bspModelName is not None and not hitTester.isBspModelLoaded():
                    prereqs.append(hitTester.bspModelName)

            self.appearance = VehicleAppearance.VehicleAppearance()
            prereqs += self.appearance.prerequisites(self)
            return prereqs

    def onEnterWorld(self, prereqs):
        descr = self.typeDescriptor
        descr.keepPrereqs(prereqs)
        self.__prereqs = prereqs
        self.__prevDamageStickers = frozenset()
        self.__prevPublicStateModifiers = frozenset()
        self.targetFullBounds = True
        player = BigWorld.player()
        for hitTester in descr.getHitTesters():
            hitTester.loadBspModel()
            player.hitTesters.add(hitTester)

        player.initSpace()
        player.vehicle_onEnterWorld(self)
        return

    def onLeaveWorld(self):
        self.__stopExtras()
        BigWorld.player().vehicle_onLeaveWorld(self)
        assert not self.isStarted
        return

    def showShooting(self, isPredictedShot=False):
        if not self.isStarted:
            return
        else:
            if not isPredictedShot and self.isPlayer and not BigWorld.player().isWaitingForShot:
                return
            extra = self.typeDescriptor.extrasDict['shoot']
            data = self.extras.get(extra.index)
            if data is not None:
                extra.stop(data)
            extra.startFor(self)
            if not isPredictedShot and self.isPlayer:
                BigWorld.player().cancelWaitingForShot()
            return

    def showDamageFromShot(self, attackerID, points, effectsIndex):
        if not self.isStarted:
            return
        else:
            descr = self.typeDescriptor
            effectsDescr = vehicles.g_cache.shotEffects[effectsIndex]
            player = BigWorld.player()
            firstHitDir = None
            maxShotResult = None
            for point in points:
                (compName, shotResult, startPoint, endPoint) = _decodeSegment(descr, point)
                if startPoint == endPoint:
                    continue
                if maxShotResult is None or shotResult > maxShotResult:
                    maxShotResult = shotResult
                (stages, effects, _) = effectsDescr[self.__shotResultToEffectGroup[shotResult]]
                hitTester = getattr(descr, compName)['hitTester']
                hitTestRes = hitTester.localHitTest(startPoint, endPoint)
                if not hitTestRes:
                    continue
                minDist = hitTestRes[0]
                for hitTestRes in hitTestRes:
                    dist = hitTestRes[0]
                    if dist < minDist:
                        minDist = dist

                dir = endPoint - startPoint
                dir.normalise()
                rot = Math.Matrix()
                rot.setRotateYPR((dir.yaw, dir.pitch, 0.0))
                mat = Math.Matrix()
                mat.setTranslate(startPoint + dir * minDist)
                mat.preMultiply(rot)
                showFullscreenEffs = self.isPlayer and self.isAlive()
                self.appearance.modelsDesc[compName]['boundEffects'].addNew(mat, effects, stages, showShockWave=showFullscreenEffs, showFlashBang=showFullscreenEffs)
                if firstHitDir is None:
                    compMatrix = Math.Matrix(self.appearance.modelsDesc[compName]['model'].matrix)
                    firstHitDir = compMatrix.applyVector(dir)
                    self.appearance.receiveShotImpulse(firstHitDir, effectsDescr['targetImpulse'])
                    self.appearance.executeHitVibrations(maxShotResult)

            if not self.isAlive():
                return
            if self.isPlayer:
                if maxShotResult >= 0:
                    if player.inputHandler.aim is not None and firstHitDir is not None:
                        player.inputHandler.aim.showHit(firstHitDir.yaw)
                return
            if attackerID == player.playerVehicleID and maxShotResult is not None:
                marker = getattr(self, 'marker', None)
                if marker is not None:
                    manager = g_windowsManager.battleWindow.vMarkersManager
                    manager.updateMarkerState(marker, 'hit_pierced' if maxShotResult >= SHOT_RESULT.MIN_HIT else 'hit')
                if self.isAlive() and player.arena.vehicles[self.id]['isAlive']:
                    player.playShotResultNotification(maxShotResult, self)
            return

    __shotResultToEffectGroup = ('armorRicochet', 'armorResisted', 'armorHit', 'armorHit',
                                 'armorCriticalHit')

    def showFireStartedMessage(self, attackerID):
        player = BigWorld.player()
        if attackerID == player.playerVehicleID:
            player.playStartedFire(self)
        return

    def showDamageFromExplosion(self, attackerID, center, effectsIndex):
        if not self.isStarted:
            return
        else:
            impulse = vehicles.g_cache.shotEffects[effectsIndex]['targetImpulse'] / 4.0
            dir = self.position - center
            dir.normalise()
            self.appearance.receiveShotImpulse(dir, impulse)
            self.appearance.executeHitVibrations(SHOT_RESULT.MAX_HIT + 1)
            if not self.isAlive():
                return
            if self.id == attackerID:
                return
            player = BigWorld.player()
            if self.isPlayer:
                if player.inputHandler.aim:
                    attacker = BigWorld.entities.get(attackerID)
                    if attacker is not None:
                        startPos = attacker.position
                    else:
                        startPos = player.arena.positions.get(attackerID)
                        if startPos is None:
                            startPos = center
                    gYaw = (self.position - Math.Vector3(startPos)).yaw
                    player.inputHandler.aim.showHit(gYaw)
            if attackerID == player.playerVehicleID:
                manager = g_windowsManager.battleWindow.vMarkersManager
                manager.updateMarkerState(self.marker, 'hit_pierced')
                if self.isAlive() and player.arena.vehicles[self.id]['isAlive']:
                    player.playShotResultNotification(SHOT_RESULT.MAX_HIT + 1, self)
            return

    def showVehicleCollisionEffect(self, pos):
        if not self.isStarted:
            return
        hullAppearance = self.appearance.modelsDesc['hull']
        invWorldMatrix = Math.Matrix(hullAppearance['model'].matrix)
        invWorldMatrix.invert()
        rot = Math.Matrix()
        rot.setRotateYPR((random.uniform(-3.14, 3.14), random.uniform(-1.5, 1.5), 0.0))
        mat = Math.Matrix()
        mat.setTranslate(pos)
        mat.preMultiply(rot)
        mat.postMultiply(invWorldMatrix)
        (stages, effects, _) = random.choice(self.typeDescriptor.type.effects['collision'])
        hullAppearance['boundEffects'].addNew(mat, effects, stages, entity=self)
        self.appearance.executeRammingVibrations()
        return

    def set_damageStickers(self, prev=None):
        if self.isStarted:
            prev = self.__prevDamageStickers
            curr = frozenset(self.damageStickers)
            self.__prevDamageStickers = curr
            for sticker in prev.difference(curr):
                self.appearance.removeDamageSticker(sticker)

            descr = self.typeDescriptor
            for sticker in curr.difference(prev):
                self.appearance.addDamageSticker(sticker, *_decodeSegment(descr, sticker))

        return

    def set_publicStateModifiers(self, prev=None):
        if self.isStarted:
            prev = self.__prevPublicStateModifiers
            curr = frozenset(self.publicStateModifiers)
            self.__prevPublicStateModifiers = curr
            self.__updateModifiers(curr.difference(prev), prev.difference(curr))
        return

    def set_engineMode(self, prev):
        if self.isStarted:
            self.appearance.changeEngineMode(self.engineMode)
        return

    def set_health(self, prev):
        if self.isStarted:
            self.appearance.onVehicleHealthChanged()
            if self.health <= 0 and self.isCrewActive:
                self.__onVehicleDeath()
            if not self.isPlayer:
                g_windowsManager.battleWindow.vMarkersManager.onVehicleHealthChanged(self.marker, self.health, self.typeDescriptor.maxHealth)
        return

    def set_isCrewActive(self, prev):
        if self.isStarted:
            self.appearance.onVehicleHealthChanged()
            if not self.isCrewActive and self.health > 0:
                self.__onVehicleDeath()
            if not self.isPlayer:
                g_windowsManager.battleWindow.vMarkersManager.onVehicleHealthChanged(self.marker, self.health, self.typeDescriptor.maxHealth)
        return

    def getComponents(self):
        res = []
        vehicleDescr = self.typeDescriptor
        m = Math.Matrix()
        m.setIdentity()
        res.append((vehicleDescr.chassis, m))
        hullOffset = vehicleDescr.chassis['hullPosition']
        m = Math.Matrix()
        m.setTranslate(-hullOffset)
        res.append((vehicleDescr.hull, m))
        turretYaw = Math.Matrix(self.appearance.turretMatrix).yaw
        turretMatrix = Math.Matrix()
        turretMatrix.setTranslate(-hullOffset - vehicleDescr.hull['turretPositions'][0])
        m = Math.Matrix()
        m.setRotateY(-turretYaw)
        turretMatrix.postMultiply(m)
        res.append((vehicleDescr.turret, turretMatrix))
        gunPitch = Math.Matrix(self.appearance.gunMatrix).pitch
        gunMatrix = Math.Matrix()
        gunMatrix.setTranslate(-vehicleDescr.turret['gunPosition'])
        m = Math.Matrix()
        m.setRotateX(-gunPitch)
        gunMatrix.postMultiply(m)
        gunMatrix.preMultiply(turretMatrix)
        res.append((vehicleDescr.gun, gunMatrix))
        return res

    def collideSegment(self, startPoint, endPoint):
        if not self.typeDescriptor.hitTester.mayHit(startPoint, endPoint, self.position):
            return
        else:
            worldToVehMatrix = Math.Matrix(self.model.matrix)
            worldToVehMatrix.invert()
            startPoint = worldToVehMatrix.applyPoint(startPoint)
            endPoint = worldToVehMatrix.applyPoint(endPoint)
            res = None
            for (compDescr, compMatrix) in self.getComponents():
                collisions = compDescr['hitTester'].localHitTest(compMatrix.applyPoint(startPoint), compMatrix.applyPoint(endPoint))
                if collisions is None:
                    continue
                for (dist, _, hitAngleCos, matKind) in collisions:
                    if res is None or res[0] >= dist:
                        res = (
                         dist, hitAngleCos, compDescr['armor'].get(matKind, (0, ))[0])

            return res

    def isAlive(self):
        return self.isCrewActive and self.health > 0

    def getSpeed(self):
        return self.filter.speedInfo.value[0]

    def startVisual(self):
        assert not self.isStarted
        avatar = BigWorld.player()
        self.appearance.start(self, self.__prereqs)
        self.__prereqs = None
        self.appearance.changeEngineMode(self.engineMode)
        self.appearance.onVehicleHealthChanged()
        if self.isPlayer:
            BigWorld.wgAddEdgeDetectEntity(self, 0, True)
            self.appearance.turretMatrix.target = avatar.gunRotator.turretMatrix
            self.appearance.gunMatrix.target = avatar.gunRotator.gunMatrix
            self.filter.allowStrafeCompensation = False
        else:
            self.marker = g_windowsManager.battleWindow.vMarkersManager.createMarker(self.proxy)
            self.filter.allowStrafeCompensation = True
        self.isStarted = True
        self.set_publicStateModifiers()
        self.set_damageStickers()
        if not self.isAlive():
            self.__onVehicleDeath(True)
        minimap = g_windowsManager.battleWindow.minimap
        minimap.notifyVehicleStart(self.id)
        return

    def stopVisual(self):
        assert self.isStarted
        if self.isPlayer:
            BigWorld.wgDelEdgeDetectEntity(self)
        self.__stopExtras()
        if hasattr(self, 'marker'):
            manager = g_windowsManager.battleWindow.vMarkersManager
            manager.destroyMarker(self.marker)
            self.marker = -1
        self.appearance.destroy()
        self.appearance = None
        self.isStarted = False
        minimap = g_windowsManager.battleWindow.minimap
        minimap.notifyVehicleStop(self.id)
        return

    def showPlayerMovementCommand(self, flags):
        if not self.isStarted:
            return
        powerMode = self.engineMode[0]
        if flags == 0 and powerMode != 0:
            self.appearance.changeEngineMode((1, 0))
            return
        if flags != 0 and powerMode != 0:
            self.appearance.changeEngineMode((3, flags))
            return
        return

    def onPushed(self, x, z):
        distSqr = BigWorld.player().position.distSqrTo(self.position)
        if distSqr > 1600.0:
            self.filter.setPosition(x, z)
        return

    def _isDestructibleBroken(self, chunkID, itemIndex, matKind, itemFilename, itemScale, vehSpeed):
        desc = AreaDestructibles.g_cache.getDescByFilename(itemFilename)
        if desc is None:
            return False
        else:
            ctrl = AreaDestructibles.g_destructiblesManager.getController(chunkID)
            if ctrl is None:
                return False
            if ctrl.isDestructibleBroken(itemIndex, matKind, desc['type']):
                return True
            mass = self.typeDescriptor.physics['weight']
            instantDamage = 0.5 * mass * vehSpeed * vehSpeed * 0.00015
            if desc['type'] == DestructiblesCache.DESTR_TYPE_STRUCTURE:
                moduleDesc = desc['modules'].get(matKind)
                if moduleDesc is None:
                    return False
                refHealth = moduleDesc['health']
            else:
                instantDamage *= math.pow(mass / AreaDestructibles.g_cache.unitVehicleMass, desc['kineticDamageCorrection'])
                refHealth = desc['health']
            return DestructiblesCache.scaledDestructibleHealth(itemScale, refHealth) < instantDamage

    def __stopExtras(self):
        extraTypes = self.typeDescriptor.extras
        for (index, data) in self.extras.items():
            extraTypes[index].stop(data)

        if self.extras:
            LOG_CODEPOINT_WARNING()
        return

    def __updateModifiers(self, addedExtras, removedExtras):
        descr = self.typeDescriptor
        for idx in removedExtras:
            data = self.extras.get(idx)
            if data is not None:
                data['extra'].stop(data)
            else:
                LOG_WARNING('Attempt to remove non-existent EntityExtra data', self.typeDescriptor.name, self.typeDescriptor.extras[idx].name)

        for idx in addedExtras:
            if idx < 0 or idx >= len(self.typeDescriptor.extras):
                LOG_WARNING('Attempt to add unknown EntityExtra', self.typeDescriptor.name, idx)
            else:
                try:
                    self.typeDescriptor.extras[idx].startFor(self)
                except Exception:
                    LOG_CURRENT_EXCEPTION()

        return

    def __onVehicleDeath(self, isDeadStarted=False):
        if not self.isPlayer:
            manager = g_windowsManager.battleWindow.vMarkersManager
            manager.updateMarkerState(self.marker, 'dead', isDeadStarted)
        self.stopHornSound(True)
        self.filter.allowLagProcessing = False
        return

    def playHornSound(self, hornID):
        if not self.isStarted:
            return
        else:
            hornDesc = vehicles.g_cache.horns().get(hornID)
            if hornDesc is None:
                return
            self.stopHornSound(True)
            self.__hornSounds = []
            self.__hornMode = hornDesc['mode']
            model = self.appearance.modelsDesc['turret']['model']
            for sndEventId in hornDesc['sounds']:
                self.__hornSounds.append(model.getSound(sndEventId))

            if self.__hornSounds[0] is not None:
                self.__hornSounds[0].play()
                if self.__hornMode == 'continuous' and hornDesc['maxDuration'] > 0:
                    self.__stopHornSoundCallback = BigWorld.callback(hornDesc['maxDuration'], self.stopHornSound)
            return

    def stopHornSound(self, forceSilence=False):
        if not forceSilence and self.__hornMode == 'twoSounds':
            if self.__hornSounds[1] is not None:
                self.__hornSounds[1].play()
        else:
            for snd in self.__hornSounds:
                if snd is not None:
                    snd.stop()

            self.__hornSounds = (None, )
        if self.__stopHornSoundCallback is not None:
            BigWorld.cancelCallback(self.__stopHornSoundCallback)
            self.__stopHornSoundCallback = None
        self.__hornMode = ''
        return

    def isHornActive(self):
        if self.__hornMode == 'twoSounds':
            return True
        else:
            anySoundPlaying = False
            for snd in self.__hornSounds:
                if snd is not None:
                    state = snd.state
                    if state is not None and state.find('playing') != -1:
                        return True

            return False


def _decodeSegment(vehicleDescr, segment):
    compIdx = int(segment & 65280) >> 8
    if compIdx == 0:
        componentName = 'chassis'
        bbox = vehicleDescr.chassis['hitTester'].bbox
    elif compIdx == 1:
        componentName = 'hull'
        bbox = vehicleDescr.hull['hitTester'].bbox
    elif compIdx == 2:
        componentName = 'turret'
        bbox = vehicleDescr.turret['hitTester'].bbox
    elif compIdx == 3:
        componentName = 'gun'
        bbox = vehicleDescr.gun['hitTester'].bbox
    else:
        LOG_CODEPOINT_WARNING(compIdx)
    min = Math.Vector3(bbox[0])
    delta = bbox[1] - min
    segStart = min + Math.Vector3(*(k * (segment >> shift & 255) / 255.0 for (k, shift) in izip(delta, xrange(16, 33, 8))))
    segEnd = min + Math.Vector3(*(k * (segment >> shift & 255) / 255.0 for (k, shift) in izip(delta, xrange(40, 57, 8))))
    dir = segEnd - segStart
    dir.normalise()
    segStart -= dir * 0.01
    segEnd += dir * 0.01
    return (
     componentName, segment & 255, segStart, segEnd)


return

# okay decompiling C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\Vehicle.pyc
