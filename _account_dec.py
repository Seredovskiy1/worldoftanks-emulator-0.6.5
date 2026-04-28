# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/Account.py
# Compiled at: 2011-06-29 06:52:48
import BigWorld, Keys, cPickle, zlib, Event, items, nations, AccountSyncData, AccountCommands, ClientPrebattle, constants, Settings, helpers.i18n
from account_helpers import Inventory, DossierCache, Shop, Stats, Trader
from itertools import izip
from ConnectionManager import connectionManager
from PlayerEvents import g_playerEvents as events
from constants import *
from streamIDs import CHAT_INITIALIZATION_ID, RangeStreamIDCallbacks, STREAM_ID_CHAT_MAX, STREAM_ID_CHAT_MIN
from debug_utils import *
from ContactInfo import ContactInfo
from ClientChat import ClientChat
from ChatManager import chatManager
from OfflineMapCreator import g_offlineMapCreator
from gui.Scaleform import VoiceChatInterface
from adisp import process

class PlayerAccount(BigWorld.Entity, ClientChat):
    __DEFAULT_ACCOUNT_TYPE_ID = ACCOUNT_TYPE.BASE
    __onStreamCompletePredef = {(AccountCommands.REQUEST_ID_PREBATTLES): 'receivePrebattles', 
       (AccountCommands.REQUEST_ID_PREBATTLE_ROSTER): 'receivePrebattleRoster'}
    __rangeStreamIDCallbacks = RangeStreamIDCallbacks()

    def version_eu6501(self):
        return

    def __init__(self):
        global _g_accountRepository
        ClientChat.__init__(self)
        self.__rangeStreamIDCallbacks.addRangeCallback((STREAM_ID_CHAT_MIN, STREAM_ID_CHAT_MAX), '_ClientChat__receiveStreamedData')
        if g_offlineMapCreator.Active():
            self.name = 'offline_account'
        if _g_accountRepository is None:
            _g_accountRepository = _AccountRepository(self.name)
        self.contactInfo = _g_accountRepository.contactInfo
        self.syncData = _g_accountRepository.syncData
        self.inventory = _g_accountRepository.inventory
        self.stats = _g_accountRepository.stats
        self.trader = _g_accountRepository.trader
        self.shop = _g_accountRepository.shop
        self.dossierCache = _g_accountRepository.dossierCache
        self.syncData.setAccount(self)
        self.inventory.setAccount(self)
        self.stats.setAccount(self)
        self.trader.setAccount(self)
        self.shop.setAccount(self)
        self.dossierCache.setAccount(self)
        self.prebattle = None
        self.specialPrebattles = _g_accountRepository.specialPrebattles
        self.clanMembers = _g_accountRepository.clanMembers
        self.isInQueue = False
        self.__onCmdResponse = {}
        self.__onStreamComplete = {}
        return

    @process
    def onBecomePlayer(self):
        LOG_DEBUG('Account.onBecomePlayer()')
        self.isPlayer = True
        self.databaseID = None
        self.inputHandler = AccountInputHandler()
        BigWorld.resetEntityManager(True, False)
        BigWorld.clearAllSpaces()
        self.syncData.onAccountBecomePlayer()
        self.inventory.onAccountBecomePlayer()
        self.stats.onAccountBecomePlayer()
        self.trader.onAccountBecomePlayer()
        self.shop.onAccountBecomePlayer()
        self.dossierCache.onAccountBecomePlayer()
        chatManager.switchPlayerProxy(self)
        events.onAccountBecomePlayer()
        yield VoiceChatInterface.g_instance.initialize(self.serverSettings['vivoxDomain'])
        yield VoiceChatInterface.g_instance.requestCaptureDevices()
        return

    def onBecomeNonPlayer(self):
        LOG_DEBUG('Account.onBecomeNonPlayer()')
        if not (hasattr(self, 'isPlayer') and self.isPlayer):
            return
        else:
            self.isPlayer = False
            chatManager.switchPlayerProxy(None)
            self.syncData.onAccountBecomeNonPlayer()
            self.inventory.onAccountBecomeNonPlayer()
            self.stats.onAccountBecomeNonPlayer()
            self.trader.onAccountBecomeNonPlayer()
            self.shop.onAccountBecomeNonPlayer()
            self.dossierCache.onAccountBecomeNonPlayer()
            self.__cancelCommands()
            self.syncData.setAccount(None)
            self.inventory.setAccount(None)
            self.stats.setAccount(None)
            self.trader.setAccount(None)
            self.shop.setAccount(None)
            self.dossierCache.setAccount(None)
            events.onAccountBecomeNonPlayer()
            del self.inputHandler
            return

    def onCmdResponse(self, requestID, resultID):
        callback = self.__onCmdResponse.pop(requestID, None)
        if callback is not None:
            callback(requestID, resultID)
        return

    def onCmdResponseExt(self, requestID, resultID, ext):
        ext = cPickle.loads(ext)
        if resultID == AccountCommands.RES_SHOP_DESYNC:
            self.shop.synchronize(ext.get('shopRev', None))
        callback = self.__onCmdResponse.pop(requestID, None)
        if callback is not None:
            callback(requestID, resultID, ext)
        return

    def onKickedFromServer(self, reason, isBan, expiryTime):
        LOG_MX('onKickedFromServer', reason, isBan, expiryTime)
        from gui.Scaleform.Disconnect import Disconnect
        Disconnect.showKick(reason, isBan, expiryTime)
        return

    def onStreamComplete(self, id, data):
        callback = self.__rangeStreamIDCallbacks.getCallbackForStreamID(id)
        if callback is not None:
            getattr(self, callback)(id, data)
            return
        else:
            callback = self.__onStreamCompletePredef.get(id, None)
            if callback is not None:
                getattr(self, callback)(True, data)
                return
            callback = self.__onStreamComplete.pop(id, None)
            if callback is not None:
                callback(True, data)
            return

    def onEnqueued(self):
        LOG_DEBUG('onEnqueued')
        self.isInQueue = True
        events.onEnqueued()
        return

    def onEnqueueFailure(self, errorCode):
        LOG_DEBUG('onEnqueueFailure', errorCode)
        events.onEnqueueFailure(errorCode)
        return

    def onDequeued(self):
        LOG_DEBUG('onDequeued')
        self.isInQueue = False
        events.onDequeued()
        return

    def onArenaCreated(self):
        LOG_DEBUG('onArenaCreated')
        self.prebattle = None
        events.isPlayerEntityChanging = True
        events.onArenaCreated()
        events.onPlayerEntityChanging()
        return

    def onArenaJoinFailure(self, errorCode):
        LOG_DEBUG('onArenaJoinFailure', errorCode)
        events.isPlayerEntityChanging = False
        events.onPlayerEntityChangeCanceled()
        events.onArenaJoinFailure(errorCode)
        return

    def onPrebattleJoined(self, prebattleID):
        self.prebattle = ClientPrebattle.ClientPrebattle(prebattleID)
        events.onPrebattleJoined()
        return

    def onPrebattleJoinFailure(self, errorCode):
        LOG_MX('onPrebattleJoinFailure', errorCode)
        events.onPrebattleJoinFailure(errorCode)
        return

    def onPrebattleLeft(self):
        LOG_MX('onPrebattleLeft')
        self.prebattle = None
        events.onPrebattleLeft()
        return

    def onKickedFromQueue(self):
        LOG_DEBUG('onKickedFromQueue')
        events.onKickedFromQueue()
        return

    def onKickedFromArena(self, reasonCode):
        LOG_DEBUG('onKickedFromArena', reasonCode)
        events.isPlayerEntityChanging = False
        events.onPlayerEntityChangeCanceled()
        events.onKickedFromArena(reasonCode)
        return

    def onKickedFromPrebattle(self, reasonCode):
        LOG_DEBUG('onKickedFromPrebattle', reasonCode)
        self.prebattle = None
        events.onKickedFromPrebattle(reasonCode)
        return

    def handleKeyEvent(self, event):
        return False

    def showGUI(self, ctx):
        ctx = cPickle.loads(ctx)
        LOG_MX('showGUI', ctx)
        self.databaseID = ctx['databaseID']
        if 'prebattleID' in ctx:
            self.prebattle = ClientPrebattle.ClientPrebattle(ctx['prebattleID'])
        if 'queueID' in ctx:
            self.isInQueue = True
        if 'serverUTC' in ctx:
            import helpers.time_utils as tm
            tm.setTimeCorrection(ctx['serverUTC'])
        events.isPlayerEntityChanging = False
        events.onAccountShowGUI(ctx)
        return

    def receiveQueueInfo(self, randomsQueueInfo, companiesQueueInfo):
        events.onQueueInfoReceived(randomsQueueInfo, companiesQueueInfo)
        return

    def receivePrebattles(self, isSuccess, data):
        if isSuccess:
            try:
                data = zlib.decompress(data)
                (type, count, prebattles) = cPickle.loads(data)
            except:
                LOG_CURRENT_EXCEPTION()
                isSuccess = False

        if not isSuccess:
            type, count, prebattles = 0, 0, []
        events.onPrebattlesListReceived(type, count, prebattles)
        return

    def receivePrebattleRoster(self, isSuccess, data):
        if isSuccess:
            try:
                data = zlib.decompress(data)
                (prebattleID, rosterAsList) = cPickle.loads(data)
            except:
                LOG_CURRENT_EXCEPTION()
                isSuccess = False

        if not isSuccess:
            prebattleID, rosterAsList = 0, []
        events.onPrebattleRosterReceived(prebattleID, rosterAsList)
        return

    def receiveActiveArenas(self, arenas):
        events.onArenaListReceived(arenas)
        return

    def receiveServerStats(self, stats):
        events.onServerStatsReceived(stats)
        return

    def updatePrebattle(self, updateType, argStr):
        if self.prebattle is not None:
            self.prebattle.update(updateType, argStr)
        return

    def update(self, diff):
        self._update(True, cPickle.loads(diff))
        return

    def resyncDossiers(self):
        self.dossierCache.resynchronize()
        return

    def requestQueueInfo(self, queueType):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_REQ_QUEUE_INFO, queueType, 0, 0)
        return

    def requestPrebattles(self, type, sort_key, idle, start, end):
        if not events.isPlayerEntityChanging:
            self.base.doCmdIntArr(AccountCommands.REQUEST_ID_PREBATTLES, AccountCommands.CMD_REQ_PREBATTLES, [type, sort_key, int(idle), start, end])
        return

    def requestPrebattlesByName(self, type, idle, creatorMask):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt2Str(AccountCommands.REQUEST_ID_PREBATTLES, AccountCommands.CMD_REQ_PREBATTLES_BY_CREATOR, type, int(idle), creatorMask)
        return

    def requestPrebattleRoster(self, prebattleID):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_PREBATTLE_ROSTER, AccountCommands.CMD_REQ_PREBATTLE_ROSTER, prebattleID, 0, 0)
        return

    def requestArenaList(self):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_REQ_ARENA_LIST, 0, 0, 0)
        return

    def requestServerStats(self):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        return

    def requestPlayerDossier(self, name, dossierType, ownerID, callback):
        if dossierType == DOSSIER_TYPE.ACCOUNT:
            ownerID = 0
        if not events.isPlayerEntityChanging:
            proxy = lambda requestID, resultID: self.__onPlayerDossierResponse(requestID, resultID, dossierType, callback)
            self._doCmdInt2Str(AccountCommands.CMD_REQ_PLAYER_DOSSIER, dossierType, ownerID, name, proxy)
        return

    def requestPlayerClanInfo(self, name, callback):
        if not events.isPlayerEntityChanging:
            proxy = lambda requestID, resultID: self.__onPlayerClanInfoResponse(requestID, resultID, callback)
            self._doCmdInt2Str(AccountCommands.CMD_REQ_PLAYER_CLAN_INFO, 0, 0, name, proxy)
        return

    def enqueueForArena(self, vehInvID, arenaTypeID=0, queueType=QUEUE_TYPE.RANDOMS):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_ENQUEUE_FOR_ARENA, vehInvID, arenaTypeID, queueType)
        return

    def dequeue(self):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_DEQUEUE, 0, 0, 0)
        return

    def createArenaFromQueue(self):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_FORCE_QUEUE, 0, 0, 0)
        return

    def createArena(self, typeID, roundLength):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_CREATE_ARENA, roundLength, 0, typeID)
        return

    def joinArena(self, arenaID, team, vehInvID):
        if not events.isPlayerEntityChanging:
            self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_JOIN_ARENA, arenaID, team, vehInvID)
        return

    def prb_createTraining(self, arenaTypeID, roundLength, isOpened, comment):
        if events.isPlayerEntityChanging:
            return
        self.base.createTraining(arenaTypeID, roundLength, isOpened, comment)
        return

    def prb_createSquad(self):
        if events.isPlayerEntityChanging:
            return
        self.base.createSquad()
        return

    def prb_createCompany(self, isOpened, comment):
        if events.isPlayerEntityChanging:
            return
        self.base.createCompany(isOpened, comment)
        return

    def prb_join(self, prebattleID):
        if events.isPlayerEntityChanging:
            return
        self.base.doCmdInt3(AccountCommands.REQUEST_ID_NO_RESPONSE, AccountCommands.CMD_PRB_JOIN, prebattleID, 0, 0)
        return

    def prb_leave(self, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_LEAVE, 0, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_ready(self, vehInvID, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_READY, vehInvID, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_notReady(self, state, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_NOT_READY, state, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_assign(self, playerID, roster, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_ASSIGN, playerID, roster, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_changeArena(self, arenaTypeID, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_CH_ARENA, arenaTypeID, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_changeRoundLength(self, roundLength, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_CH_ROUND, roundLength, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_changeOpenStatus(self, isOpened, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_OPEN, 1 if isOpened else 0, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_changeComment(self, comment, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdStr(AccountCommands.CMD_PRB_CH_COMMENT, comment, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_changeArenaVoip(self, arenaVoipChannels, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_CH_ARENAVOIP, arenaVoipChannels, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_teamReady(self, team, force, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_TEAM_READY, team, 1 if force else 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_teamNotReady(self, team, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_TEAM_NOT_READY, team, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def prb_kick(self, playerID, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt3(AccountCommands.CMD_PRB_KICK, playerID, 0, 0, (lambda requestID, resultID: callback(resultID)))
        return

    def challengeCaptcha(self, challenge, response, callback):
        if events.isPlayerEntityChanging:
            return
        self._doCmdInt2Str(AccountCommands.CMD_CAPTCHA_CHALLENGE, len(challenge), 0, challenge + response, (lambda requestID, resultID, errorCode: callback(resultID, errorCode)))
        return

    def setLanguage(self, language):
        self._doCmdStr(AccountCommands.CMD_SET_LANGUAGE, language, None)
        return

    def _doCmdStr(self, cmd, str, callback):
        self.__doCmd('doCmdStr', cmd, callback, str)
        return

    def _doCmdInt3(self, cmd, int1, int2, int3, callback):
        self.__doCmd('doCmdInt3', cmd, callback, int1, int2, int3)
        return

    def _doCmdInt4(self, cmd, int1, int2, int3, int4, callback):
        self.__doCmd('doCmdInt4', cmd, callback, int1, int2, int3, int4)
        return

    def _doCmdInt2Str(self, cmd, int1, int2, str, callback):
        self.__doCmd('doCmdInt2Str', cmd, callback, int1, int2, str)
        return

    def _doCmdIntArr(self, cmd, arr, callback):
        self.__doCmd('doCmdIntArr', cmd, callback, arr)
        return

    def _makeTradeOffer(self, passwd, flags, dstDBID, validSec, price, srcWares, srcItemCount, callback):
        if _g_accountRepository is None:
            return
        else:
            requestID = self.__getRequestID()
            if requestID is None:
                return
            if callback is not None:
                self.__onCmdResponse[requestID] = callback
            self.base.makeTradeOfferByClient(requestID, passwd, flags, dstDBID, validSec, price, srcWares, srcItemCount)
            return

    def _update(self, triggerEvents, diff):
        LOG_MX('_update', diff if triggerEvents else 'full sync')
        isFullSync = diff.get('prevRev', None) is None
        self.syncData.revision = diff.get('rev', 0)
        self.inventory.synchronize(isFullSync, diff)
        self.stats.synchronize(isFullSync, diff)
        self.trader.synchronize(isFullSync, diff)
        self.__synchronizeCacheDict(self.specialPrebattles, diff.get('account', None), 'specialPrebattles', events.onSpecPrebattlesListChanged)
        self.__synchronizeCacheDict(self.clanMembers, diff.get('cache', None), 'clanMembers', events.onClanMembersListChanged)
        if triggerEvents:
            events.onClientUpdated(diff)
            if not isFullSync:
                for vehTypeCompDescr in diff.get('stats', {}).get('eliteVehicles', ()):
                    events.onVehicleBecomeElite(vehTypeCompDescr)

                for (vehInvID, lockReason) in diff.get('cache', {}).get('vehsLock', {}).iteritems():
                    if lockReason is None:
                        lockReason = AccountCommands.LOCK_REASON.NONE
                    events.onVehicleLockChanged(vehInvID, lockReason)

        return

    def _subscribeForStream(self, requestID, callback):
        self.__onStreamComplete[requestID] = callback
        return

    def __getRequestID(self):
        if _g_accountRepository is None:
            return
        else:
            _g_accountRepository.requestID += 1
            if _g_accountRepository.requestID >= AccountCommands.REQUEST_ID_UNRESERVED_MAX:
                _g_accountRepository.requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN
            return _g_accountRepository.requestID

    def __doCmd(self, doCmdMethod, cmd, callback, *args):
        if _g_accountRepository is None:
            return
        else:
            requestID = self.__getRequestID()
            if requestID is None:
                return
            if callback is not None:
                self.__onCmdResponse[requestID] = callback
            getattr(self.base, doCmdMethod)(requestID, cmd, *args)
            return

    def __cancelCommands(self):
        for (requestID, callback) in self.__onCmdResponse.iteritems():
            try:
                callback(requestID, AccountCommands.RES_NON_PLAYER)
            except:
                LOG_CURRENT_EXCEPTION()

        self.__onCmdResponse.clear()
        for callback in self.__onStreamComplete.itervalues():
            try:
                callback(False, None)
            except:
                LOG_CURRENT_EXCEPTION()

        self.__onStreamComplete.clear()
        return

    def __onPlayerDossierResponse(self, requestID, resultID, dossierType, callback):
        if resultID != AccountCommands.RES_STREAM:
            try:
                callback(resultID, '')
            except:
                LOG_CURRENT_EXCEPTION()

        else:
            proxy = lambda isSuccess, data: self.__onPlayerDossierStream(isSuccess, data, dossierType, callback)
            self._subscribeForStream(requestID, proxy)
        return

    def __onPlayerDossierStream(self, isSuccess, data, dossierType, callback):
        if isSuccess:
            try:
                data = cPickle.loads(zlib.decompress(data))
                resultID = AccountCommands.RES_STREAM
            except:
                if data is None:
                    LOG_CODEPOINT_WARNING()
                else:
                    LOG_CURRENT_EXCEPTION()
                isSuccess = False

        if not isSuccess:
            resultID = AccountCommands.RES_FAILURE
            data = None
        if data is None:
            dossierCompDescr = ''
        elif dossierType == DOSSIER_TYPE.ACCOUNT:
            dossierCompDescr = data
        else:
            dossierCompDescr = data[1][3]
        try:
            callback(resultID, dossierCompDescr)
        except:
            LOG_CURRENT_EXCEPTION()

        return

    def __onPlayerClanInfoResponse(self, requestID, resultID, callback):
        if resultID != AccountCommands.RES_STREAM:
            try:
                callback(resultID, 0, None)
            except:
                LOG_CURRENT_EXCEPTION()

        else:
            proxy = lambda isSuccess, data: self.__onPlayerClanInfoStream(isSuccess, data, callback)
            self._subscribeForStream(requestID, proxy)
        return

    def __onPlayerClanInfoStream(self, isSuccess, data, callback):
        if isSuccess:
            try:
                data = cPickle.loads(zlib.decompress(data))
                resultID = AccountCommands.RES_STREAM
            except:
                if data is None:
                    LOG_CODEPOINT_WARNING()
                else:
                    LOG_CURRENT_EXCEPTION()
                isSuccess = False

        if not isSuccess:
            resultID = AccountCommands.RES_FAILURE
            data = (0, None)
        try:
            callback(resultID, *data)
        except:
            LOG_CURRENT_EXCEPTION()

        return

    def __synchronizeCacheDict(self, repDict, diffDict, key, event):
        if diffDict is None:
            return
        else:
            repl = diffDict.get('%s_r' % key, None)
            if repl is not None:
                repDict.clear()
                repDict.update(repl)
            diff = diffDict.get(key, None)
            if diff is not None:
                for (k, v) in diff.iteritems():
                    if v is None:
                        repDict.pop(k, None)
                    else:
                        repDict[k] = v

            if repl is not None or diff is not None:
                event()
            return


Account = PlayerAccount

class AccountInputHandler():

    def handleKeyEvent(self, event):
        return False

    def handleMouseEvent(self, dx, dy, dz):
        return False


class _AccountRepository(object):

    def __init__(self, name):
        self.contactInfo = ContactInfo()
        self.syncData = AccountSyncData.AccountSyncData()
        self.inventory = Inventory.Inventory(self.syncData)
        self.stats = Stats.Stats(self.syncData)
        self.trader = Trader.Trader(self.syncData)
        self.shop = Shop.Shop()
        self.dossierCache = DossierCache.DossierCache(name)
        self.specialPrebattles = {}
        self.clanMembers = {}
        self.requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN
        return


def _delAccountRepository():
    global _g_accountRepository
    LOG_MX('_delAccountRepository')
    _g_accountRepository = None
    return


_g_accountRepository = None
connectionManager.onDisconnected += _delAccountRepository
return

# okay decompiling C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\Account.pyc
