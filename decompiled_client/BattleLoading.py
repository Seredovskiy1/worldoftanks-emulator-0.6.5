# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/gui/Scaleform/BattleLoading.py
# Compiled at: 2011-05-26 15:49:26
from debug_utils import LOG_DEBUG
import BigWorld, constants
from helpers.tips import getTip
from gui.Scaleform.windows import UIInterface
from gui.Scaleform.Waiting import Waiting
from account_helpers.AccountPrebattle import AccountPrebattle
from helpers.i18n import makeString
from messenger.gui import MessengerDispatcher
_MAP_BG_SOURCE = '../maps/icons/map/screen/%s.dds'
_CONTOUR_ICONS_MASK = '../maps/icons/vehicle/contour/%(unicName)s.tga'

class BattleLoading(UIInterface):

    def __init__(self):
        self.onLoaded = None
        self.callbackId = None
        self.__arena = BigWorld.player().arena
        self.__progress = 0
        UIInterface.__init__(self)
        return

    def populateUI(self, proxy):
        UIInterface.populateUI(self, proxy)
        if self.__arena:
            self.__arena.onNewVehicleListReceived += self.__updatePlayers
            self.__arena.onNewStatisticsReceived += self.__updatePlayers
            self.__arena.onVehicleAdded += self.__updatePlayers
            self.__arena.onVehicleStatisticsUpdate += self.__updatePlayers
            self.__arena.onVehicleKilled += self.__updatePlayers
            self.__arena.onAvatarReady += self.__updatePlayers
            MessengerDispatcher.g_instance.users.onUsersRosterReceived += self.__updatePlayers
        self.uiHolder.addExternalCallbacks({'loading.getData': (self.__populateData)})
        self.uiHolder.movie.backgroundAlpha = 1.0
        self.__populateData()
        self.isSpaceLoaded()
        Waiting.hide('loadPage')
        Waiting.close()
        return

    def dispossessUI(self):
        if self.callbackId is not None:
            BigWorld.cancelCallback(self.callbackId)
            self.callbackId = None
        if self.__arena:
            self.__arena.onNewVehicleListReceived -= self.__updatePlayers
            self.__arena.onNewStatisticsReceived -= self.__updatePlayers
            self.__arena.onVehicleAdded -= self.__updatePlayers
            self.__arena.onVehicleStatisticsUpdate -= self.__updatePlayers
            self.__arena.onVehicleKilled -= self.__updatePlayers
            self.__arena.onAvatarReady -= self.__updatePlayers
            MessengerDispatcher.g_instance.users.onUsersRosterReceived -= self.__updatePlayers
        self.uiHolder.removeExternalCallbacks('loading.getData')
        self.__arena = None
        UIInterface.dispossessUI(self)
        return

    def isSpaceLoaded(self):
        self.callbackId = None
        status = BigWorld.spaceLoadStatus()
        if status > self.__progress:
            self.__progress = status
            self.__setProgress(status)
        if status < 1.0:
            self.callbackId = BigWorld.callback(0.5, self.isSpaceLoaded)
            return
        else:
            BigWorld.player().onSpaceLoaded()
            self.isLoaded()
            return

    def isLoaded(self):
        self.callbackId = None
        if not BigWorld.worldDrawEnabled():
            self.callbackId = BigWorld.callback(0.5, self.isLoaded)
            return
        else:
            from gui.WindowsManager import g_windowsManager
            BigWorld.callback(0.1, g_windowsManager.showBattle)
            return

    def __setProgress(self, value):
        self.call('loading.setProgress', [value])
        return

    def __populateData(self, callbackID=None):
        arena = getattr(BigWorld.player(), 'arena', None)
        if arena:
            self.call('loading.setMap', [arena.typeDescriptor.name])
            self.call('loading.setMapBG', [_MAP_BG_SOURCE % arena.typeDescriptor.typeName])
            if arena.extraData:
                self.call('loading.setBattleType', [arena.guiType, AccountPrebattle.getPrebattleDescription(arena.extraData or {})])
            else:
                self.call('loading.setBattleType', [arena.guiType, '#menu:loading/battleTypes/%d' % arena.guiType])
        self.call('loading.setTip', [getTip()])
        self.__updatePlayers()
        return

    def __updatePlayers(self, *args):
        stat = {1: [], 2: []}
        squads = {1: {}, 2: {}}
        player = BigWorld.player()
        if player is None:
            return
        else:
            if self.__arena is None:
                return
            vehicles = self.__arena.vehicles
            for (vId, vData) in vehicles.items():
                team = vData['team']
                name = vData['name']
                if vData['clanAbbrev']:
                    name = name + '[%s]' % vData['clanAbbrev']
                vShortName = vData['vehicleType'].type.shortUserString
                vName = vData['vehicleType'].type.userString
                vIcon = _CONTOUR_ICONS_MASK % {'unicName': (vData['vehicleType'].type.name.replace(':', '-'))}
                isAlive = vData['isAlive'] and vData['isAvatarReady']
                if vData['prebattleID'] != 0:
                    if vData['prebattleID'] not in squads[team].keys():
                        squads[team][vData['prebattleID']] = 1
                    else:
                        squads[team][vData['prebattleID']] += 1
                balanceWeight = vData['vehicleType'].balanceWeight
                user = MessengerDispatcher.g_instance.users.getUser(vData['accountDBID'], name)
                stat[team].append([
                 name, vIcon, vShortName, not isAlive, vId, vData['prebattleID'],
                 balanceWeight, vName, not vData['isAlive'], vData['name'], vData['accountDBID'],
                 user.isMuted()])

            squadsSorted = {}
            squadsSorted[1] = sorted(squads[1].iteritems(), cmp=(lambda x, y: cmp(x[0], y[0])))
            squadsSorted[2] = sorted(squads[2].iteritems(), cmp=(lambda x, y: cmp(x[0], y[0])))
            squadsFiltered = {}
            squadsFiltered[1] = [_[1] for (id, num) in squadsSorted[1] if 1 < num < 4 if self.__arena.guiType == constants.ARENA_GUI_TYPE.RANDOM]
            squadsFiltered[2] = [_[2] for (id, num) in squadsSorted[2] if 1 < num < 4 if self.__arena.guiType == constants.ARENA_GUI_TYPE.RANDOM]
            for team in (1, 2):
                playerVehicleID = None
                if hasattr(player, 'playerVehicleID'):
                    playerVehicleID = player.playerVehicleID
                value = [
                 'team2', -1, -1]
                data = sorted(stat[team], cmp=_playerComparator)
                for item in data:
                    item[5] = squadsFiltered[team].index(item[5]) + 1 if item[5] in squadsFiltered[team] else 0
                    if item[9] == player.name and value[1] == -1 or item[4] == playerVehicleID:
                        value[1] = item[4]
                        if item[5] > 0:
                            value[2] = item[5]
                        value[0] = 'team1'
                        self.setTeams(team)
                    value.extend(item[:-4])
                    value.append(item[10])
                    value.append(item[11])

                self.call('loading.setTeam', value)

            return

    def setTeams(self, myTeam):
        arena = getattr(BigWorld.player(), 'arena', None)
        if arena:
            extraData = arena.extraData or {}
            team1 = extraData.get('opponents', {}).get('%s' % myTeam, {}).get('name', '#menu:loading/team1')
            team2 = extraData.get('opponents', {}).get('2' if myTeam == 1 else '1', {}).get('name', '#menu:loading/team2')
            self.call('loading.setTeams', [team1, team2])
        return


def _playerComparator(x1, x2):
    if x1[8] < x2[8]:
        return -1
    if x1[8] > x2[8]:
        return 1
    if x1[6] < x2[6]:
        return 1
    if x1[6] > x2[6]:
        return -1
    if x1[7] < x2[7]:
        return -1
    if x1[7] > x2[7]:
        return 1
    if x1[9] < x2[9]:
        return -1
    if x1[9] > x2[9]:
        return 1
    return 0


return
