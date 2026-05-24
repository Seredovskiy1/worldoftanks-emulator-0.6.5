                          

                                          

                                                                                                          

                                                            

                                  

import GUI, BigWorld, weakref

from account_helpers.AccountSettings import AccountSettings

import constants, CommandMapping

from windows import BattleWindow

from SettingsInterface import SettingsInterface

from debug_utils import LOG_CODEPOINT_WARNING, LOG_CURRENT_EXCEPTION, LOG_DEBUG, LOG_ERROR

from gui import DEPTH_OF_Battle, DEPTH_OF_VehicleMarker, TANKMEN_ROLES_ORDER_DICT

from gui.Scaleform.Flash import Flash

from helpers import i18n

from helpers.i18n import makeString

from PlayerEvents import g_playerEvents

from battle_heroes import ACHIEVEMENT_TEXTS as heroesTexts, ACHIEVEMENT_NAMES as heroesNames

from gui.Scaleform.utils.sound import Sound

from MemoryCriticalController import g_critMemHandler

from items.vehicles import NUM_EQUIPMENT_SLOTS, VEHICLE_CLASS_TAGS

from messenger import MESSENGER_I18N_FILE

from messenger.UsersManager import USERS_ROSTER_ACTIONS

from messenger.gui import MessengerDispatcher

from gui.Scaleform import VoiceChatInterface

from gui.Scaleform.Minimap import Minimap

_CONTOUR_ICONS_MASK = '../maps/icons/vehicle/contour/%(unicName)s.tga'



class Battle(BattleWindow):

    __userTransferUserMsgKeys = {(USERS_ROSTER_ACTIONS.AddToFriend): ('#%s:client/information/addToFriends/message' % MESSENGER_I18N_FILE), 

       (USERS_ROSTER_ACTIONS.AddToIgnored): ('#%s:client/information/addToIgnored/message' % MESSENGER_I18N_FILE), 

       (USERS_ROSTER_ACTIONS.RemoveFromFriend): ('#%s:client/information/removeFromFriends/message' % MESSENGER_I18N_FILE), 

       (USERS_ROSTER_ACTIONS.RemoveFromIgnored): ('#%s:client/information/removeFromIgnored/message' % MESSENGER_I18N_FILE)}

    teamBasesPanel = property((lambda self: self.__teamBasesPanel))

    consumablesPanel = property((lambda self: self.__consumablesPanel))

    damagePanel = property((lambda self: self.__damagePanel))

    vMarkersManager = property((lambda self: self.__vMarkersManager))

    vErrorsPanel = property((lambda self: self.__vErrorsPanel))

    vMsgsPanel = property((lambda self: self.__vMsgsPanel))

    pMsgsPanel = property((lambda self: self.__pMsgsPanel))

    minimap = property((lambda self: self.__minimap))

    _speakPlayers = {}

    __playersVehicleID = {}

    _prebattleEnum = {}



    def __init__(self):

        self.__timerCallBackId = None

        self.__vehicles = {}

        self.__arena = BigWorld.player().arena

        BattleWindow.__init__(self, 'battle.swf')

        self.__timerSound = Sound('/GUI/notifications_FX/timer')

        self.__isTimerVisible = False

        self.component.wg_inputKeyMode = 1

        self.component.position.z = DEPTH_OF_Battle

        self.movie.backgroundAlpha = 0

        self.addFsCallbacks({'battle.leave': (self.onExitBattle)})

        self.addExternalCallbacks({'battle.showCursor': (self.cursorVisibility), 

           'Battle.UsersRoster.AddToFriends': (self.onAddToFriends), 

           'Battle.UsersRoster.RemoveFromFriends': (self.onRemoveFromFriends), 

           'Battle.UsersRoster.AddToIgnored': (self.onAddToIgnored), 

           'Battle.UsersRoster.RemoveFromIgnored': (self.onRemoveFromIgnored), 

           'Battle.UsersRoster.AddMuted': (self.onSetMuted), 

           'Battle.UsersRoster.RemoveMuted': (self.onUnsetMuted)})

        BigWorld.wg_setRedefineKeysMode(False)

        return



    def speakingPlayersReset(self):

        for id in self._speakPlayers.keys():

            self.setPlayerSpeaking(id, False)



        self._speakPlayers.clear()

        return



    def onAddToFriends(self, callbackId, uid, userName):

        self._usersManager.addFriend(uid, userName)

        return



    def onRemoveFromFriends(self, callbackId, uid):

        self._usersManager.removeFriend(uid)

        return



    def onAddToIgnored(self, callbackId, uid, userName):

        self._usersManager.addIgnored(uid, userName)

        return



    def onRemoveFromIgnored(self, callbackId, uid):

        self._usersManager.removeIgnored(uid)

        return



    def onSetMuted(self, callbackId, uid, userName):

        self._usersManager.setMuted(uid, userName)

        return



    def onUnsetMuted(self, callbackId, uid):

        self._usersManager.unsetMuted(uid)

        return



    def setVisible(self, bool):

        LOG_DEBUG('[Battle] visible', bool)

        self.component.visible = bool

        self.__vMarkersManager.visible(bool)

        return



    def __onUsersRosterUpdate(self, action, user):

        messageKey = self.__userTransferUserMsgKeys.get(action)

        if messageKey is not None:

            MessengerDispatcher.g_instance.currentWindow.showActionFailureMesssage(makeString(messageKey) % user.userName)

        return



    def afterCreate(self):

        LOG_DEBUG('[Battle] afterCreate')

        setattr(self.movie, '_global.wg_isShowLanguageBar', constants.SHOW_LANGUAGE_BAR)

        BattleWindow.afterCreate(self)

        g_playerEvents.onBattleResultsReceived += self.__showFinalStatsResults

        self._usersManager = MessengerDispatcher.g_instance.users

        self._usersManager.onUsersRosterUpdate += self.__updatePlayers

        self._usersManager.onUsersRosterUpdate += self.__onUsersRosterUpdate

        self._usersManager.onUsersRosterReceived += self.__updatePlayers

        if self.__arena:

            self.__arena.onPeriodChange += self.__onSetArenaTime

            self.__arena.onNewVehicleListReceived += self.__updatePlayers

            self.__arena.onNewStatisticsReceived += self.__updatePlayers

            self.__arena.onVehicleAdded += self.__updatePlayers

            self.__arena.onVehicleStatisticsUpdate += self.__updatePlayers

            self.__arena.onVehicleKilled += self.__updatePlayers

            self.__arena.onAvatarReady += self.__updatePlayers

            self.__arena.onTeamKiller += self.__onTeamKiller

        self.proxy = weakref.proxy(self)

        self._speakPlayers.clear()

        VoiceChatInterface.g_instance.populateUI(self.proxy)

        self.__settingsInterface = SettingsInterface()

        self.__settingsInterface.populateUI(self.proxy)

        self.__teamBasesPanel = TeamBasesPanel(self.proxy)

        self.__fragCorrelation = FragCorrelationPanel(self.proxy)

        self.__debugPanel = DebugPanel(self.proxy)

        self.__consumablesPanel = ConsumablesPanel(self.proxy)

        self.__damagePanel = DamagePanel(self.proxy)

        self.__vMarkersManager = VehicleMarkersManager(self.proxy)

        self.__ingameHelp = IngameHelp(self.proxy)

        self.__minimap = Minimap(self.proxy)

        self.__vErrorsPanel = FadingMessagesPanel(self.proxy, 'VehicleErrorsPanel', 'gui/vehicle_errors_panel.xml')

        self.__vMsgsPanel = FadingMessagesPanel(self.proxy, 'VehicleMessagesPanel', 'gui/vehicle_messages_panel.xml')

        self.__pMsgsPanel = FadingMessagesPanel(self.proxy, 'PlayerMessagesPanel', 'gui/player_messages_panel.xml')

        self.__teamBasesPanel.start()

        self.__debugPanel.start()

        self.__consumablesPanel.start()

        self.__damagePanel.start()

        self.__vMarkersManager.start()

        self.__ingameHelp.start()

        self.__vErrorsPanel.start()

        self.__vMsgsPanel.start()

        self.__pMsgsPanel.start()

        self.__minimap.start()

        self.__initMemoryCriticalHandlers()

        MessengerDispatcher.g_instance.battleMessenger.start(self.proxy)

        from game import g_guiResetters

        g_guiResetters.add(self.__onRecreateDevice)

        g_guiResetters.add(self.__settingsInterface.onRecreateDevice)

        from game import g_repeatKeyHandlers

        g_repeatKeyHandlers.add(self.component.handleKeyEvent)

        self.__onRecreateDevice()

        self.__setPlayerInfo()

        self.__updatePlayers()

        self.__populateData()

        VoiceChatInterface.g_instance.onVoiceChatInitFailed += self.onVoiceChatInitFailed

        BigWorld.callback(1, self.__setArenaTime)

        self.movie.setFocussed()

        return



    def beforeDelete(self):

        LOG_DEBUG('[Battle] beforeDelete')

        self.__destroyMemoryCriticalHandlers()

        if VoiceChatInterface.g_instance:

            VoiceChatInterface.g_instance.dispossessUI()

        if self.component:

            from game import g_repeatKeyHandlers

            g_repeatKeyHandlers.discard(self.component.handleKeyEvent)

        self.__teamBasesPanel.destroy()

        self.__debugPanel.destroy()

        self.__consumablesPanel.destroy()

        self.__damagePanel.destroy()

        self.__vMarkersManager.destroy()

        self.__ingameHelp.destroy()

        self.__vErrorsPanel.destroy()

        self.__vMsgsPanel.destroy()

        self.__pMsgsPanel.destroy()

        self.__minimap.destroy()

        self.__timerSound.stop()

        MessengerDispatcher.g_instance.battleMessenger.destroy()

        BattleWindow.beforeDelete(self)

        g_playerEvents.onBattleResultsReceived -= self.__showFinalStatsResults

        self._usersManager.resetTeamkillers()

        self._usersManager.onUsersRosterUpdate -= self.__updatePlayers

        self._usersManager.onUsersRosterReceived -= self.__updatePlayers

        self._usersManager.onUsersRosterUpdate -= self.__onUsersRosterUpdate

        if self.__arena:

            self.__arena.onPeriodChange -= self.__onSetArenaTime

            self.__arena.onNewVehicleListReceived -= self.__updatePlayers

            self.__arena.onNewStatisticsReceived -= self.__updatePlayers

            self.__arena.onVehicleAdded -= self.__updatePlayers

            self.__arena.onVehicleStatisticsUpdate -= self.__updatePlayers

            self.__arena.onVehicleKilled -= self.__updatePlayers

            self.__arena.onAvatarReady -= self.__updatePlayers

            self.__arena.onTeamKiller -= self.__onTeamKiller

        self.__arena = None

        VoiceChatInterface.g_instance.onVoiceChatInitFailed -= self.onVoiceChatInitFailed

        from game import g_guiResetters

        g_guiResetters.discard(self.__onRecreateDevice)

        g_guiResetters.discard(self.__settingsInterface.onRecreateDevice)

        self.__settingsInterface.dispossessUI()

        self.__settingsInterface = None

        return



    def onVoiceChatInitFailed(self):

        self.call('VoiceChat.initFailed', [])

        return



    def bindCommands(self):

        self.__consumablesPanel.bindCommands()

        self.__ingameHelp.buildCmdMapping()

        return



    def setPlayerSpeaking(self, accountDBID, flag):

        self._speakPlayers[accountDBID] = flag

        self.__callEx('setPlayerSpeaking', [accountDBID, flag])

        vID = self.__playersVehicleID.get(accountDBID)

        if vID > 0:

            self.__vMarkersManager.showDynamic(vID, flag)

        else:

            LOG_ERROR('Can not find vehicle ID by accountDBID = ', accountDBID)

        return



    def isPlayerSpeaking(self, accountDBID):

        return self._speakPlayers.get(accountDBID, False)



    def showPostmortemTips(self):

        self.__callEx('showPostmortemTips', [1.0, 5.0, 1.0])

        return



    def cursorVisibility(self, callbackId, visible):

        BigWorld.player().setForcedGuiControlMode(visible, False)

        return



    def onExitBattle(self, arg):

        LOG_DEBUG('onExitBattle')

        arena = getattr(BigWorld.player(), 'arena', None)

        if arena:

            BigWorld.player().leaveArena()

        return



    def __setPlayerInfo(self):

        player = BigWorld.player()

        (playerName, vTypeName) = ('', '')

        if player:

            vID = player.playerVehicleID

            vInfo = self.__arena.vehicles.get(vID)

            if vInfo is not None:

                playerName = vInfo['name']

                clanAbbrev = vInfo['clanAbbrev']

                if clanAbbrev is not None and len(clanAbbrev) > 0:

                    playerName = '%s[%s]' % (playerName, clanAbbrev)

                vTypeName = vInfo['vehicleType'].type.userString

        self.__callEx('setPlayerInfo', [playerName, vTypeName])

        return



    def __populateData(self):

        from account_helpers.AccountPrebattle import AccountPrebattle

        arena = getattr(BigWorld.player(), 'arena', None)

        arenaData = [4, 5, 4, 4, 4]

        if arena:

            arenaData = [

             arena.typeDescriptor.name]

            if arena.extraData:

                arenaData.extend([arena.guiType, AccountPrebattle.getPrebattleDescription(arena.extraData or {})])

            else:

                arenaData.extend([arena.guiType, '#menu:loading/battleTypes/%d' % arena.guiType])

            extraData = arena.extraData or {}

            team1 = extraData.get('opponents', {}).get(1, {}).get('name', '#menu:loading/team1')

            team2 = extraData.get('opponents', {}).get(2, {}).get('name', '#menu:loading/team2')

            arenaData.extend([team1, team2])

        self.__callEx('arenaData', arenaData)

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

                vName = vData['vehicleType'].type.userString

                vShortName = vData['vehicleType'].type.shortUserString

                vIcon = _CONTOUR_ICONS_MASK % {'unicName': (vData['vehicleType'].type.name.replace(':', '-'))}

                isAlive = vData['isAlive']

                isAvatarReady = vData['isAvatarReady']

                vState = 0

                if isAlive:

                    vState |= 1

                if isAvatarReady:

                    vState |= 2

                if vData['prebattleID'] > 0:

                    if vData['prebattleID'] not in squads[team].keys():

                        squads[team][vData['prebattleID']] = 1

                    else:

                        squads[team][vData['prebattleID']] += 1

                vStats = self.__arena.statistics.get(vId, None)

                frags = 0 if vStats is None else vStats['frags']

                balanceWeight = vData['vehicleType'].balanceWeight

                if vData['clanAbbrev']:

                    userDisplayName = name + '[%s]' % vData['clanAbbrev']

                else:

                    userDisplayName = name

                self.__vehicles.update({vId: (userDisplayName, vName)})

                user = self._usersManager.getUser(vData['accountDBID'], vData['name'])

                self.__playersVehicleID[vData['accountDBID']] = vId

                if vData.get('isTeamKiller', False):

                    self._usersManager.markAsTeamkiller(vData['accountDBID'], True)

                stat[team].append([

                 name, vIcon, vShortName, vState, frags, vId, vData['prebattleID'],

                 vData['clanAbbrev'], self._speakPlayers.get(vData['accountDBID'], False),

                 user.uid, user.roster, user.isMuted(), vData['isTeamKiller'],

                 not isAlive, balanceWeight])



            squadsSorted = {1: (sorted(squads[1].iteritems(), cmp=(lambda x, y: cmp(x[0], y[0])))), 

               2: (sorted(squads[2].iteritems(), cmp=(lambda x, y: cmp(x[0], y[0]))))}

            squadsFiltered = {1: [_[1] for (id, num) in squadsSorted[1] if 1 < num < 4 if self.__arena.guiType == constants.ARENA_GUI_TYPE.RANDOM], 

               2: [_[2] for (id, num) in squadsSorted[2] if 1 < num < 4 if self.__arena.guiType == constants.ARENA_GUI_TYPE.RANDOM]}

            teamFrags = [

             0, 0]

            for team in (1, 2):

                value = ['team%d' % team, -1, -1]

                data = sorted(stat[team], cmp=_playerComparator)

                for item in data:

                    teamFrags[team - 1] += item[4]

                    sNumber = squadsFiltered[team].index(item[6]) + 1 if item[6] in squadsFiltered[team] else 0

                    if sNumber > 0:

                        self._prebattleEnum[item[6]] = ('squad-{0:d}').format(sNumber)

                    item[6] = sNumber

                    if item[5] == player.playerVehicleID and item[6] > 0:

                        value[2] = item[6]

                    value.extend(item[:-2])

                    if team != player.team:

                        value[-1] = False



                if team == player.team:

                    value[1] = player.playerVehicleID

                self.__callEx('setTeam', value)



            playerTeam = player.team - 1

            enemyTeam = 1 - playerTeam

            self.__fragCorrelation.updateFrags(teamFrags[playerTeam], teamFrags[enemyTeam])

            return



    def __showFinalStatsResults(self, isActiveVehicle, vehInvID, results):

        if isActiveVehicle:

            if not self.__vehicles:

                self.__updatePlayers()

            if results['killerID']:

                killer = makeString('#ingame_gui:statistics/final/lifeInfo/dead', '%s (%s)' % self.__vehicles.get(results['killerID'], ('n/a',

                                                                                                                                        'n/a')))

            else:

                killer = makeString('#ingame_gui:statistics/final/lifeInfo/alive')

            stats = [

             results['xp'], results['credits'], results['repair'], results['xpFactor'] == 2 and results['xp'] > 0, self.__vehicles.get(BigWorld.player().playerVehicleID, ('n/a', 'n/a'))[0], killer]

            results['damaged'] = list(set(results['damaged']).difference(set(results['killed'])))

            for key in ('killed', 'damaged', 'spotted'):

                lt = set()

                for id in results[key]:

                    lt.add('%s (%s)' % self.__vehicles.get(id, ('n/a', 'n/a')))



                stats.append(len(lt))

                stats.extend(lt)



            hl = set()

            if results.has_key('achieveIndices'):

                for (i, heroId) in enumerate(results['achieveIndices']):

                    herolist = [

                     makeString(heroesTexts[heroesNames[heroId]])]

                    if results.has_key('heroVehicleIDs') and len(results['heroVehicleIDs']) > i:

                        if self.__arena.vehicles.get(results['heroVehicleIDs'][i], False):

                            if not self.__arena.vehicles[results['heroVehicleIDs'][i]]['isAlive']:

                                herolist[0] += ' ' + makeString('#ingame_gui:statistics/final/personal/postmortem')

                        herolist.extend(self.__vehicles.get(results['heroVehicleIDs'][i], ('n/a',

                                                                                           'n/a')))

                        hl.add('%s - %s (%s)' % tuple(herolist))



            stats.append(len(hl))

            stats.extend(hl)

            for key in ('shots', 'hits', 'shotsReceived'):

                stats.append(makeString('#ingame_gui:statistics/final/personal/' + key, results[key]))



            for key in ('capturePoints', 'droppedCapturePoints'):

                stats.append(makeString('#ingame_gui:statistics/final/personal/' + key, min(results[key], 100)))



            self.__callEx('showFinalStatistic', stats)

            BigWorld.player().setForcedGuiControlMode(True)

        return



    def __showFinalStats(self, winnerTeam, reason):

        if hasattr(BigWorld.player(), 'team'):

            reason = makeString('#ingame_gui:statistics/final/reasons/reason%d' % reason)

            status = 'tie' if winnerTeam == 0 else 'win' if (winnerTeam == BigWorld.player().team) else 'lose'

            status = makeString('#ingame_gui:statistics/final/status/%s' % status, reason)

            self.__callEx('showStatus', [status])

        return



    def __onSetArenaTime(self, *args):

        if self.__timerCallBackId is not None:

            BigWorld.cancelCallback(self.__timerCallBackId)

        self.__setArenaTime()

        return



    def __setArenaTime(self):

        self.__timerCallBackId = None

        if self.__arena is None:

            return

        else:

            period = self.__arena.period

            arenaLength = int(self.__arena.periodEndTime - BigWorld.serverTime())

            arenaLength = arenaLength if arenaLength > 0 else 0

            self.__callEx('timerBar.setTotalTime', [arenaLength])

            if period == constants.ARENA_PERIOD.WAITING:

                self.__callEx('timerBig.setTimer', [makeString('#ingame_gui:timer/waiting')])

                self.__isTimerVisible = True

            elif period == constants.ARENA_PERIOD.PREBATTLE:

                self.__callEx('timerBig.setTimer', [makeString('#ingame_gui:timer/starting'), arenaLength])

                self.__isTimerVisible = True

                if not self.__timerSound.isPlaying:

                    self.__timerSound.play()

            elif period == constants.ARENA_PERIOD.BATTLE and self.__isTimerVisible:

                self.__isTimerVisible = False

                self.__timerSound.stop()

                self.__callEx('timerBig.setTimer', [makeString('#ingame_gui:timer/started')])

                self.__callEx('timerBig.hide')

            elif period == constants.ARENA_PERIOD.AFTERBATTLE:

                self.__showFinalStats(*self.__arena.periodAdditionalInfo)

            if arenaLength > 1:

                self.__timerCallBackId = BigWorld.callback(1, self.__setArenaTime)

            return



    def __onTeamKiller(self, vID):

        self.__updatePlayers(vID)

        self.__vMarkersManager.setTeamKiller(vID)

        return



    def __onRecreateDevice(self):

        self.call('Stage.Update', list(GUI.screenResolution()))

        return



    def __callEx(self, funcName, args=None):

        self.call('battle.' + funcName, args)

        return



    def __initMemoryCriticalHandlers(self):

        for message in g_critMemHandler.messages:

            self.__onMemoryCritical(message)



        g_critMemHandler.onMemCrit += self.__onMemoryCritical

        return



    def __destroyMemoryCriticalHandlers(self):

        g_critMemHandler.onMemCrit -= self.__onMemoryCritical

        return



    def __onMemoryCritical(self, message):

        self.__vMsgsPanel.showMessage(message[1])

        return



    def __getEntityUserString(self, entityName):

        player = BigWorld.player()

        if player and player.isVehicleAlive:

            extra = player.vehicleTypeDescriptor.extrasDict.get(entityName + 'Health')

            if extra is None:

                return entityName

            return extra.deviceUserString

        else:

            return



    def _showTankmanIsSafeMessage(self, entityName):

        if not self.__consumablesPanel.hasMedkit():

            return

        tankman = self.__getEntityUserString(entityName)

        if tankman:

            self.__vErrorsPanel.showMessage('medkitTankmanIsSafe', {'entity': tankman})

        return



    def _showDeviceIsNotDamagedMessage(self, entityName):

        if not self.__consumablesPanel.hasRepairkit():

            return

        if entityName == 'chassis':

            device = i18n.makeString('#ingame_gui:devices/chassis')

        else:

            device = self.__getEntityUserString(entityName)

        if device:

            self.__vErrorsPanel.showMessage('repairkitDeviceIsNotDamaged', {'entity': device})

        return





class TeamBasesPanel(object):



    def __init__(self, parentUI):

        self.__ui = parentUI

        msgCommonPart = '#ingame_gui:player_messages/'

        self.__msgAllyBaseCaptured = i18n.makeString(msgCommonPart + 'ally_base_captured_notification')

        self.__msgEnemyBaseCaptured = i18n.makeString(msgCommonPart + 'enemy_base_captured_notification')

        self.__msgAllyBaseCapturedBy = i18n.makeString(msgCommonPart + 'ally_base_captured_by_notification')

        self.__msgEnemyBaseCapturedBy = i18n.makeString(msgCommonPart + 'enemy_base_captured_by_notification')

        return



    def start(self):

        LOG_DEBUG('TeamBasesPanel.start')

        arena = BigWorld.player().arena

        arena.onTeamBasePointsUpdate += self.__onTeamBasePointsUpdate

        arena.onTeamBaseCaptured += self.__onTeamBaseCaptured

        arena.onPeriodChange += self.__onPeriodChange

        return



    def destroy(self):

        LOG_DEBUG('TeamBasesPanel.destroy')

        arena = getattr(BigWorld.player(), 'arena', None)

        if arena is not None:

            arena.onTeamBasePointsUpdate -= self.__onTeamBasePointsUpdate

            arena.onTeamBaseCaptured -= self.__onTeamBaseCaptured

            arena.onPeriodChange -= self.__onPeriodChange

        return



    def __onTeamBasePointsUpdate(self, team, baseID, points):

        if team not in (1, 2):

            return

            isAllyTeam = True if team == BigWorld.player().team else False

            teamType = 'red' if isAllyTeam else 'green'

            points or self.__callFlash(teamType, 'show', [False])

        else:

            msg = self.__msgAllyBaseCapturedBy if isAllyTeam else self.__msgEnemyBaseCapturedBy

            self.__callFlash(teamType, 'show', [True])

            self.__callFlash(teamType, 'updateProgress', [points / 100.0])

            self.__callFlash(teamType, 'updateTitle', [i18n.convert(msg)])

            self.__callFlash(teamType, 'updateTimer', ['%s' % points])

        return



    def __onTeamBaseCaptured(self, team):

        if team not in (1, 2):

            return

        isAllyTeam = True if team == BigWorld.player().team else False

        teamType = 'red' if isAllyTeam else 'green'

        msg = self.__msgAllyBaseCaptured if isAllyTeam else self.__msgEnemyBaseCaptured

        self.__callFlash(teamType, 'show', [True])

        self.__callFlash(teamType, 'updateTitle', [i18n.convert(msg)])

        return



    def __onPeriodChange(self, period, *args):

        if period != constants.ARENA_PERIOD.AFTERBATTLE:

            return

        self.__callFlash('red', 'show', [False])

        self.__callFlash('green', 'show', [False])

        return



    def __callFlash(self, teamType, funcName, args):

        self.__ui.call('battle.captureBar.' + teamType + '.' + funcName, args)

        return





class FragCorrelationPanel(object):



    def __init__(self, parentUI):

        self.__ui = parentUI

        _alliedTeamName = i18n.makeString('#ingame_gui:player_messages/allied_team_name')

        _enemyTeamName = i18n.makeString('#ingame_gui:player_messages/enemy_team_name')

        self.__callFlash('setTeamNames', [_alliedTeamName, _enemyTeamName])

        return



    def updateFrags(self, alliedFrags, enemyFrags):

        self.__callFlash('updateFrags', [alliedFrags, enemyFrags])

        return



    def __callFlash(self, funcName, args):

        self.__ui.call('battle.fragCorrelationBar.' + funcName, args)

        return





class DebugPanel(object):

    __UPDATE_INTERVAL = 0.01



    def __init__(self, parentUI):

        self.__ui = parentUI

        self.__timeInterval = None

        return



    def start(self):

        self.__timeInterval = _TimeInterval(self.__UPDATE_INTERVAL, '_DebugPanel__update', weakref.proxy(self))

        self.__timeInterval.start()

        self.__update()

        return



    def destroy(self):

        self.__timeInterval.stop()

        return



    def __update(self):

        player = BigWorld.player()

        if player is None or not hasattr(player, 'playerVehicleID'):

            return

        else:

            isLaggingNow = False

            vehicle = BigWorld.entity(player.playerVehicleID)

            if vehicle is not None and isinstance(vehicle.filter, BigWorld.WGVehicleFilter):

                isLaggingNow = vehicle.filter.isLaggingNow

            ping = min(BigWorld.LatencyInfo().value[3] * 1000, 999)

            if ping < 999:

                ping = max(1, ping - 500.0 * constants.SERVER_TICK_LENGTH)

            fps = BigWorld.getFPS()[0]

            self.__ui.call('battle.debugBar.updateInfo', [int(fps), int(ping), isLaggingNow])

            return





class ConsumablesPanel(object):

    __supportedTags = set(('medkit', 'repairkit', 'stimulator', 'trigger', 'fuel',

                           'extinguisher'))

    __orderSets = {'medkit': (TANKMEN_ROLES_ORDER_DICT['enum']), 

       'repairkit': ('engine', 'ammoBay', 'gun', 'turretRotator', 'chassis', 'surveyingDevice', 'radio',

 'fuelTank')}

    __mergedEntities = {'chassis': ('leftTrack', 'rightTrack')}

    _SHELL_ICON_PATH = '../maps/icons/ammopanel/ammo/%s'

    _NO_SHELL_ICON_PATH = '../maps/icons/ammopanel/ammo/NO_%s'

    _COMMAND_MAPPING_KEY_MASK = 'CMD_AMMO_CHOICE_%d'

    _START_EQUIPMENT_SLOT_IDX = 3



    def __init__(self, parentUI):

        self.__ui = parentUI

        self.__ui.addExternalCallbacks({'battle.consumablesPanel.onClickToSlot': (self.onClickToSlot), 

           'battle.consumablesPanel.onCollapseEquipment': (self.onCollapseEquipment)})

        self.__shellKCMap = {}

        self.__equipmentKCMap = {}

        self.__equipmentTagsByIdx = {}

        self.__entitiesKCMap = {}

        self.__expandEquipmentIdx = None

        self.__processedInfo = None

        self.__emptyEquipmentSlotCount = 0

        self.__disableTurretRotator = not vehicleHasTurretRotator(BigWorld.player().vehicleTypeDescriptor)

        return



    def start(self):

        return



    def destroy(self):

        self.__ui = None

        return



    def setItemQuantityInSlot(self, idx, quantity):

        if self.__equipmentTagsByIdx.has_key(idx):

            self.__equipmentTagsByIdx[idx][1] = quantity

        self.__callFlash('setItemQuantityInSlot', [idx, quantity])

        return



    def setCoolDownTime(self, idx, timeRemaining):

        self.__callFlash('setCoolDownTime', [idx, timeRemaining])

        return



    def __getKey(self, idx):

        assert -1 < idx < 10

        cmdMappingKey = self._COMMAND_MAPPING_KEY_MASK % (idx + 1) if idx < 9 else 0

        keyCode = CommandMapping.g_instance.get(cmdMappingKey)

        keyChr = BigWorld.keyToString(keyCode)

        return (

         keyCode, keyChr)



    def bindCommands(self):

        shellKCMap = {}

        for idx in self.__shellKCMap.values():

            (keyCode, keyChr) = self.__getKey(idx)

            shellKCMap[keyCode] = idx

            self.__callFlash('setKeyToSlot', [idx, keyCode, keyChr])



        self.__shellKCMap = shellKCMap

        equipmentKCMap = {}

        for idx in self.__equipmentKCMap.values():

            (keyCode, keyChr) = self.__getKey(idx)

            equipmentKCMap[keyCode] = idx

            self.__callFlash('setKeyToSlot', [idx, keyCode, keyChr])



        self.__equipmentKCMap = equipmentKCMap

        return



    def addShellSlot(self, idx, quantity, shellDescr, piercingPower):

        kind = shellDescr['kind']

        icon = shellDescr['icon'][0]

        toolTip = i18n.convert(i18n.makeString('#ingame_gui:shells_kinds/' + kind, caliber=shellDescr['caliber'], userString=shellDescr['userString'], damage=str(int(shellDescr['damage'][0])), piercingPower=str(int(piercingPower[0]))))

        shellIconPath = self._SHELL_ICON_PATH % icon

        noShellIconPath = self._NO_SHELL_ICON_PATH % icon

        (keyCode, keyChr) = self.__getKey(idx)

        self.__shellKCMap[keyCode] = idx

        self.__callFlash('addShellSlot', [idx, keyCode, keyChr, quantity, 

         shellIconPath, noShellIconPath, 

         toolTip])

        return



    def setCurrentShell(self, idx):

        self.__callFlash('setCurrentShell', [idx])

        return



    def setNextShell(self, idx):

        self.__callFlash('setNextShell', [idx])

        return



    def hasMedkit(self):

        for (tagName, quantity) in self.__equipmentTagsByIdx.values():

            if tagName == 'medkit':

                return quantity > 0



        return False



    def hasRepairkit(self):

        for (tagName, quantity) in self.__equipmentTagsByIdx.values():

            if tagName == 'repairkit':

                return quantity > 0



        return False



    def checkEquipmentSlotIdx(self, idx):

        return max(self._START_EQUIPMENT_SLOT_IDX, idx)



    def addEquipmentSlot(self, idx, quantity, equipmentDescr):

        tags = self.__supportedTags & equipmentDescr.tags

        tagName = None

        if len(tags) == 1:

            tagName = tags.pop()

        iconPath = equipmentDescr.icon[0]

        toolTip = equipmentDescr.userString + '\n' + equipmentDescr.description

        (keyCode, keyChr) = (None, None)

        if tagName:

            (keyCode, keyChr) = self.__getKey(idx)

            self.__equipmentKCMap[keyCode] = idx

            self.__equipmentTagsByIdx[idx] = [tagName, quantity]

        self.__callFlash('addEquipmentSlot', [idx, keyCode, keyChr, tagName, 

         quantity, iconPath, 

         toolTip])

        return



    def addEmptyEquipmentSlot(self, idx):

        self.__emptyEquipmentSlotCount += 1

        toolTip = i18n.makeString('#ingame_gui:consumables_panel/equipment/tooltip/empty')

        self.__callFlash('addEquipmentSlot', [idx, 0, 0, 0, 4, 0, toolTip])

        if self.__emptyEquipmentSlotCount == NUM_EQUIPMENT_SLOTS:

            self.__callFlash('showEquipmentSlots', [False])

        return



    def expandEquipmentSlot(self, idx, tagName, entityStates):

        orderSet = self.__orderSets.get(tagName, None)

        if orderSet is None:

            if constants.IS_DEVELOPMENT:

                LOG_ERROR('Order set not determine for tag %s' % tagName)

            return

        else:

            self.__expandEquipmentIdx = idx

            self.__processedInfo = (tagName, entityStates)

            args = self.__buildEntitiesInfoList(idx, tagName, entityStates, orderSet)

            self.__callFlash('expandEquipmentSlot', args)

            return



    def updateExpandedEquipmentSlot(self, entityName, entityState):

        if self.__expandEquipmentIdx and self.__processedInfo:

            (tagName, entityStates) = self.__processedInfo

            if entityStates.has_key(entityName):

                entityStates[entityName] = entityState if entityState != 'repaired' else 'critical'

                self.__processedInfo = (tagName, entityStates)

                idx = self.__expandEquipmentIdx

                orderSet = self.__orderSets[tagName]

                args = self.__buildEntitiesInfoList(idx, tagName, entityStates, orderSet)

                self.__callFlash('updateExpandedEquipmentSlot', args)

        return



    def collapseEquipmentSlot(self, idx):

        self.__callFlash('collapseEquipmentSlot', [idx])

        return



    def __buildEntitiesInfoList(self, idx, tagName, entityStates, orderSet):

        args = [idx, tagName]

        for (entityIdx, entityName) in enumerate(orderSet):

            entityState, disabled = None, True

            (keyCode, keyChr) = self.__getKey(entityIdx)

            if self.__mergedEntities.has_key(entityName):

                realName = None

                for name in self.__mergedEntities[entityName]:

                    state = entityStates.get(name, None)

                    disabled &= not entityStates.has_key(name)

                    if realName is None and state == 'critical':

                        realName = name

                        entityState = 'critical'

                    elif state == 'destroyed':

                        realName = name

                        entityState = 'destroyed'

                        break



                if realName is not None:

                    self.__entitiesKCMap[keyCode] = (

                     realName, False)

                else:

                    self.__entitiesKCMap[keyCode] = (

                     entityName, True)

            elif entityStates.has_key(entityName):

                entityState = entityStates[entityName]

                disabled = entityName == 'turretRotator' and self.__disableTurretRotator

                if not disabled:

                    self.__entitiesKCMap[keyCode] = (

                     entityName,

                     entityState not in ('destroyed', 'critical'))

            args.extend([

             keyCode, 

             keyChr, 

             entityName, 

             entityState, 

             disabled])



        return args



    def __removeExpandEquipment(self, idx):

        if idx == self.__expandEquipmentIdx:

            self.__expandEquipmentIdx = None

            self.__processedInfo = None

            self.__entitiesKCMap.clear()

        return



    def setDisabled(self, currentShellIdx):

        self.setCoolDownTime(currentShellIdx, 0)

        self.setCurrentShell(-1)

        self.setNextShell(-1)

        for idx in self.__equipmentTagsByIdx.iterkeys():

            self.setCoolDownTime(idx, 0)



        return



    def handleKey(self, key):

        if self.__expandEquipmentIdx is not None:

            if key in self.__entitiesKCMap.keys():

                slotIdx = self.__expandEquipmentIdx

                (devName, isNormal) = self.__entitiesKCMap[key]

                if not isNormal:

                    self.collapseEquipmentSlot(slotIdx)

                    BigWorld.player().onEquipmentButtonPressed(slotIdx, deviceName=devName)

                else:

                    if self.__processedInfo is None:

                        LOG_ERROR("Can't determine equipment tag", slotIdx, devName)

                        return

                    (tagName, _) = self.__processedInfo

                    if tagName == 'medkit':

                        self.__ui._showTankmanIsSafeMessage(devName)

                    elif tagName == 'repairkit':

                        self.__ui._showDeviceIsNotDamagedMessage(devName)

                    else:

                        LOG_ERROR("Can't determine message for tag", tagName)

            return

        else:

            if key in self.__shellKCMap.keys():

                BigWorld.player().onAmmoButtonPressed(self.__shellKCMap[key])

            elif key in self.__equipmentKCMap.keys():

                BigWorld.player().onEquipmentButtonPressed(self.__equipmentKCMap[key])

            return



    def onClickToSlot(self, requestID, keyCode):

        self.handleKey(int(keyCode))

        return



    def onCollapseEquipment(self, requestID, idx):

        self.__removeExpandEquipment(int(idx))

        return



    def __callFlash(self, funcName, args=None):

        self.__ui.call('battle.consumablesPanel.%s' % funcName, args)

        return





class DamagePanel():



    def __init__(self, parentUI):

        self.__ui = parentUI

        self.__hasYawLimits = False

        self.__ui.addExternalCallbacks({'battle.damagePanel.onClickToDeviceIconButon': (self.__onClickToDeviceIconButon), 

           'battle.damagePanel.onClickToTankmenIconButon': (self.__onClickToTankmenIconButon)})

        return



    def start(self):

        vTypeDesc = BigWorld.player().vehicleTypeDescriptor

        vType = vTypeDesc.type

        self.__hasYawLimits = vTypeDesc.turret['yawLimits'] is not None

        modulesLayout = not vehicleHasTurretRotator(vTypeDesc)

        tankmensLayout = [_[1] for elem in vType.crewRoles]

        order = TANKMEN_ROLES_ORDER_DICT['plain']

        lastIdx = len(order)



        def comparator(item, other):

            itemIdx = order.index(item) if item in order else lastIdx

            otherIdx = order.index(other) if other in order else lastIdx

            return cmp(itemIdx, otherIdx)



        tankmensLayout = sorted(tankmensLayout, cmp=comparator)

        layout = tankmensLayout + [modulesLayout]

        self.__callFlash('setIconsLayout', layout)

        self.__callFlash('setMaxHealth', [vTypeDesc.maxHealth])

        if self.__hasYawLimits:

            aih = BigWorld.player().inputHandler

            isAutorotation = aih.getAutorotation() if aih is not None else True

            self.onVehicleAutorotationEnabled(isAutorotation)

        return



    def destroy(self):

        self.__ui = None

        self.__hasYawLimits = False

        return



    def updateCriticalIcon(self, type, newState):

        LOG_DEBUG('[updateCriticalIcon] type = %s state = %s' % (type, newState))

        self.__callFlash('updateCriticalIcon', [type, newState])

        return



    def onVehicleDestroyed(self):

        self.__callFlash('onVehicleDestroyed')

        self.__callFlash('onCrewDeactivated')

        return



    def onCrewDeactivated(self):

        self.__callFlash('onCrewDeactivated')

        return



    def onFireInVehicle(self, bool):

        self.__callFlash('onFireInVehicle', [bool])

        return



    def onVehicleAutorotationEnabled(self, value):

        if self.__hasYawLimits:

            self.__callFlash('onVehicleAutorotationEnabled', [value])

        return



    def updateHealth(self, cur):

        self.__callFlash('updateHealth', [cur])

        return



    def __onClickToTankmenIconButon(self, requestID, entityName, entityState):

        if entityState == 'normal':

            self.__ui._showTankmanIsSafeMessage(entityName)

            return

        BigWorld.player().onDamageIconButtonPressed('medkit', entityName)

        return



    def __onClickToDeviceIconButon(self, requestID, entityName, entityState):

        if entityState == 'normal':

            self.__ui._showDeviceIsNotDamagedMessage(entityName)

            return

        BigWorld.player().onDamageIconButtonPressed('repairkit', entityName)

        return



    def __callFlash(self, funcName, args=None):

        self.__ui.call('battle.damagePanel.' + funcName, args)

        return





class VehicleMarkersManager(Flash):

    __SWF_FILE_NAME = 'VehicleMarkersManager.swf'



    def __init__(self, parentUI):

        Flash.__init__(self, self.__SWF_FILE_NAME)

        self.__parentUI = parentUI

        self.component.wg_inputKeyMode = 2

        self.component.position.z = DEPTH_OF_VehicleMarker

        self.movie.backgroundAlpha = 0

        self.__totalIndices = 0

        self.__freeIndices = []

        self.__vMarkerDescs = []

        self.__playerVehicleID = BigWorld.player().playerVehicleID

        return



    def visible(self, bool):

        self.component.visible = bool

        return



    def showExtendedInfo(self, bool):

        self.__callFalsh('showExtendedInfo', [bool])

        return



    def start(self):

        self.active(True)

        self.__callFalsh('setPlayerSettings', [

         AccountSettings.getSettings('showVehicleIcon'),

         AccountSettings.getSettings('showVehicleLevel')])

        AccountSettings.onSettingsChanging += self.__accs_onSettingsChanging

        return



    def destroy(self):

        AccountSettings.onSettingsChanging -= self.__accs_onSettingsChanging

        self.__parentUI = None

        for desc in self.__vMarkerDescs:

            if desc is not None:

                self.component.delChild(desc.gui)

                desc.destroy()



        self.__vMarkerDesc = None

        self.close()

        return



    def createMarker(self, vProxy):

        vInfo = dict(vProxy.publicInfo)

        isFriend = vInfo['team'] == BigWorld.player().team

        pName = vInfo['name']

        vehicles = BigWorld.player().arena.vehicles

        vInfoEx = vehicles.get(vProxy.id, {})

        clanAbbrev = vInfoEx.get('clanAbbrev', '')

        pFullName = '%s[%s]' % (pName, clanAbbrev) if clanAbbrev is not None and len(clanAbbrev) > 0 else pName

        vTypeDescr = vProxy.typeDescriptor

        maxHealth = vTypeDescr.maxHealth

        mProv = vProxy.model.node('HP_gui')

        tags = set(vTypeDescr.type.tags & VEHICLE_CLASS_TAGS)

        vClass = tags.pop() if len(tags) > 0 else ''

        id = self.__calcId()

        vType = _CONTOUR_ICONS_MASK % {'unicName': (vTypeDescr.type.name.replace(':', '-'))}

        prebattleID = vInfoEx.get('prebattleID')

        prebattleInfo = None

        if isFriend and vehicles.get(self.__playerVehicleID, {}).get('prebattleID') == prebattleID:

            prebattleInfo = self.__parentUI._prebattleEnum.get(prebattleID) if self.__parentUI is not None else None

        speaking = self.__parentUI.isPlayerSpeaking(vInfoEx.get('accountDBID', 0)) if self.__parentUI is not None else None

        isTeamKiller = vInfoEx.get('isTeamKiller', False)

        self.__callFalsh('add', [

         id, vClass, vType,

         vTypeDescr.type.shortUserString, vTypeDescr.type.level,

         pName, pFullName, vProxy.health, maxHealth,

         isFriend, prebattleInfo, speaking, isTeamKiller])

        marker = GUI.WGVehicleMarkerFlash(self.movie, '_root.marker' + str(id))

        marker.wg_positionMatProv = mProv

        marker.wg_useFading = False

        marker.wg_scaleConstraints = (50, 100)

        marker.wg_scaleConst = 100

        marker.wg_scaleRatio = -0.3

        marker.wg_inputKeyMode = 2

        self.component.addChild(marker, 'marker' + str(id))

        markerDesc = _VehicleMarker(vProxy, marker)

        if len(self.__vMarkerDescs) <= id:

            self.__vMarkerDescs.append(markerDesc)

        else:

            self.__vMarkerDescs[id] = markerDesc

        return id



    def destroyMarker(self, id):

        self.__callFalsh('del', [id])

        setattr(self.component, 'marker' + str(id), None)

        self.__vMarkerDescs[id].destroy()

        self.__vMarkerDescs[id] = None

        self.__freeIndices.append(id)

        return



    def updateMarkerState(self, id, newState, isImmediate=False):

        self.__callFalsh('update', [id, newState, isImmediate])

        return



    def showActionMarker(self, id, newState):

        self.__callFalsh('showActionMarker', [id, newState])

        return



    def onVehicleHealthChanged(self, id, curHealth, maxHealth):

        self.__callFalsh('updateHealth', [id, curHealth, maxHealth])

        return



    def showDynamic(self, vID, flag):

        vehicle = BigWorld.entity(vID)

        marker = getattr(vehicle, 'marker', None)

        if marker is not None:

            self.__callFalsh('showDynamic', [marker, flag])

        return



    def setTeamKiller(self, vID):

        vehicle = BigWorld.entity(vID)

        marker = getattr(vehicle, 'marker', None)

        if marker is not None:

            self.__callFalsh('setTeamKiller', [marker])

        return



    def __calcId(self):

        result = -1

        if len(self.__freeIndices):

            result = self.__freeIndices[0]

            self.__freeIndices.remove(result)

        else:

            result = self.__totalIndices

            self.__totalIndices += 1

        if result == -1:

            LOG_CODEPOINT_WARNING()

        return result



    def __callFalsh(self, funcName, args=None):

        self.call('VehicleMarkersManager.' + funcName, args)

        return



    def __accs_onSettingsChanging(self, name):

        LOG_DEBUG('__accs_onSettingsChanging', name)

        if name in ('showVehicleIcon', 'showVehicleLevel'):

            self.__callFalsh('setPlayerSettings', [

             AccountSettings.getSettings('showVehicleIcon'),

             AccountSettings.getSettings('showVehicleLevel')])

        return





class _VehicleMarker():



    def __init__(self, vProxy, gui):

        self.vProxy = vProxy

        self.gui = gui

        self.vProxy.appearance.onModelChanged += self.__onModelChanged

        return



    def destroy(self):

        self.gui = None

        self.vProxy.appearance.onModelChanged -= self.__onModelChanged

        self.vProxy = None

        return



    def __onModelChanged(self):

        self.gui.wg_positionMatProv = self.vProxy.model.node('HP_gui')

        return





class FadingMessagesPanel(object):

    __settings = []

    __messageDict = {}



    def __init__(self, parentUI, name, cfgFileName):

        self.__ui = parentUI

        self.__name = name

        self.__pathPrefix = 'battle.' + name + '.' + '%s'

        self.__readConfig(cfgFileName)

        self.__ui.addExternalCallbacks({('battle.%s.PopulateUI' % name): (self.__onPopulateUI)})

        return



    def start(self):

        self.__callFlash('RefreshUI')

        return



    def destroy(self):

        self.__ui = None

        return



    def showMessage(self, key, args=None):

        if not self.__messageDict.has_key(key):

            self.__showMessage(key, key, args.get('colorAlias') if args else None)

            return

        else:

            msgText = self.__messageDict[key][0]

            if args is not None:

                try:

                    msgText = msgText % args

                except TypeError:

                    LOG_CURRENT_EXCEPTION()



            self.__showMessage(key, msgText, self.__messageDict[key][1])

            return



    def __showMessage(self, key, caption, color):

        if constants.IS_DEVELOPMENT:

            LOG_DEBUG('%s: show message with key = %s' % (self.__name, key))

        self.__callFlash('ShowMessage', [key, caption, color])

        return



    def __readConfig(self, cfgFileName):

        self.__settings = []

        import ResMgr

        sec = ResMgr.openSection(cfgFileName)

        if sec is None:

            raise Exception, "can not open '%s'" % cfgFileName

        self.__settings.append(sec.readInt('maxLinesCount', -1))

        direction = sec.readString('direction')

        if direction not in ('up', 'down'):

            raise Exception, 'Wrong direction value in %s' % cfgFileName

        self.__settings.append(direction)

        self.__settings.append(sec.readFloat('lifeTime'))

        self.__settings.append(sec.readFloat('alphaSpeed'))

        self.__settings.append(sec.readBool('showUniqueOnly', False))

        self.__messageDict = dict()

        for (mTag, mSec) in sec['messages'].items():

            text = mSec.readString('text')

            text = i18n.makeString(text)

            colorAlias = mSec.readString('colorAlias')

            self.__messageDict[mTag] = (text, colorAlias)



        return



    def __callFlash(self, funcName, args=None):

        self.__ui.call(self.__pathPrefix % funcName, args)

        return



    def __onPopulateUI(self, requestId):

        args = [

         requestId]

        args.extend(self.__settings)

        self.__ui.respond(args)

        return





class IngameHelp(object):

    __viewCmds = ('CMD_MOVE_FORWARD', 'CMD_MOVE_BACKWARD', 'CMD_ROTATE_LEFT', 'CMD_ROTATE_RIGHT',

                  'CMD_INCREMENT_CRUISE_MODE', 'CMD_DECREMENT_CRUISE_MODE', 'CMD_CM_VEHICLE_SWITCH_AUTOROTATION',

                  'CMD_CM_SHOOT', 'CMD_CM_LOCK_TARGET', 'CMD_CM_LOCK_TARGET_OFF',

                  'CMD_CM_ALTERNATE_MODE', 'CMD_VEHICLE_MARKERS_SHOW_INFO', 'CMD_CHAT_SHORTCAT_ATTACK',

                  'CMD_CHAT_SHORTCAT_BACKTOBASE', 'CMD_CHAT_SHORTCAT_FOLLOWME', 'CMD_CHAT_SHORTCAT_POSITIVE',

                  'CMD_CHAT_SHORTCAT_NEGATIVE', 'CMD_CHAT_SHORTCAT_HELPME', 'CMD_CHAT_SHORTCAT_ATTACK_MY_TARGET',

                  'CMD_VOICECHAT_MUTE')

    __viewCmdMapping = []



    def __init__(self, parentUI):

        self.buildCmdMapping()

        self.__ui = parentUI

        self.__ui.addExternalCallbacks({'battle.ingameHelp.getCommandMapping': (self.onGetCommandMapping)})

        return



    def start(self):

        return



    def destroy(self):

        self.__ui = None

        return



    def buildCmdMapping(self):

        cmdMap = CommandMapping.g_instance

        self.__viewCmdMapping = []

        for command in self.__viewCmds:

            key = cmdMap.get(command)

            self.__viewCmdMapping.append(command)

            self.__viewCmdMapping.append(BigWorld.keyToString(key) if key is not None else 'NONE')



        return



    def onGetCommandMapping(self, responceId, *args):

        args = [

         responceId]

        args.extend(self.__viewCmdMapping)

        self.__ui.respond(args)

        return





def _playerComparator(x1, x2):

    if x1[13] < x2[13]:

        return -1

    if x1[13] > x2[13]:

        return 1

    if x1[14] < x2[14]:

        return 1

    if x1[14] > x2[14]:

        return -1

    if x1[2] < x2[2]:

        return -1

    if x1[2] > x2[2]:

        return 1

    if x1[0] < x2[0]:

        return -1

    if x1[0] > x2[0]:

        return 1

    return 0





class _TimeInterval():



    def __init__(self, interval, funcName, scopeProxy=None):

        self.__cbId = None

        self.__interval = interval

        self.__funcName = funcName

        self.__scopeProxy = scopeProxy

        return



    def start(self):

        if self.__cbId is not None:

            LOG_ERROR('To start a new time interval You should before stop already the running time interval.')

            return

        else:

            self.__cbId = BigWorld.callback(self.__interval, self.__update)

            return



    def stop(self):

        if self.__cbId is not None:

            BigWorld.cancelCallback(self.__cbId)

            self.__cbId = None

        return



    def __update(self):

        self.__cbId = None

        self.__cbId = BigWorld.callback(self.__interval, self.__update)

        if self.__scopeProxy is not None:

            funcObj = getattr(self.__scopeProxy, self.__funcName, None)

            if funcObj is not None:

                funcObj()

        return





def vehicleHasTurretRotator(vTypeDesc):

    result = True

    if vTypeDesc.type.tags & set(['SPG', 'AT-SPG']) and len(vTypeDesc.hull.get('fakeTurrets', {}).get('battle', ())) > 0:

        result = False

    return result





return



                                                                                                     

