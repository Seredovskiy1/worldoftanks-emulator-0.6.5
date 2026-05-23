# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/ConnectionManager.py
# Compiled at: 2011-05-26 15:49:28
from constants import CLIENT_INACTIVITY_TIMEOUT, IS_DEVELOPMENT
import BigWorld
from Event import Event
from Singleton import Singleton
from enumerations import Enumeration
from debug_utils import LOG_UNEXPECTED, LOG_MX
CONNECTION_STATUS = Enumeration('Connection status', ('disconnected', 'connected',
                                                      'connectionInProgress'))

class ConnectionManager(Singleton):

    @staticmethod
    def instance():
        return ConnectionManager()

    def _singleton_init(self):
        self.searchServersCallbacks = Event()
        self.connectionStatusCallbacks = Event()
        self.connectionStatusCallbacks += self.__connectionStatusCallback
        self.__connectionStatus = CONNECTION_STATUS.disconnected
        self.onConnected = Event()
        self.onDisconnected = Event()
        self.__rawStatus = ''
        BigWorld.serverDiscovery.changeNotifier = self._searchServersHandler
        return

    def startSearchServers(self):
        BigWorld.serverDiscovery.searching = 1
        return

    def stopSearchServers(self):
        BigWorld.serverDiscovery.searching = 0
        return

    def _searchServersHandler(self):

        def _serverDottedHost(ip):
            return '%d.%d.%d.%d' % (
             ip >> 24 & 255,
             ip >> 16 & 255,
             ip >> 8 & 255,
             ip >> 0 & 255)

        def _serverNetName(details):
            name = _serverDottedHost(details.ip)
            if details.port:
                name += ':%d' % details.port
                return name
            return

        def _serverNiceName(details):
            name = details.hostName
            if not name:
                name = _serverNetName(details)
            elif details.port:
                name += ':%d' % details.port
            if details.ownerName:
                name += ' (' + details.ownerName + ')'
            return name

        servers = [_[1] for server in BigWorld.serverDiscovery.servers]
        self.searchServersCallbacks(servers)
        return

    def connect(self, host, user, password, publicKeyPath=None):
        self.disconnect()
        if len(user) > 0 and host is not None:
            self.__setConnectionStatus(CONNECTION_STATUS.connectionInProgress)

            class LoginInfo:
                pass

            login = LoginInfo()
            login.username = user
            login.password = password
            login.inactivityTimeout = CLIENT_INACTIVITY_TIMEOUT
            if publicKeyPath is not None:
                login.publicKeyPath = publicKeyPath
            BigWorld.connect(host, login, self.connectionWatcher)
            self.__setConnectionStatus(CONNECTION_STATUS.connectionInProgress)
        return

    def disconnect(self):
        if not self.isDisconnected():
            BigWorld.disconnect()
        return

    def connectionWatcher(self, stage, status, serverMsg):
        self.connectionStatusCallbacks(stage, status, serverMsg)
        return

    def __connectionStatusCallback(self, stage, status, serverMsg):
        LOG_MX('__connectionStatusCallback', stage, status, serverMsg)
        self.__rawStatus = status
        if stage == 0:
            pass
        elif stage == 1:
            if status != 'LOGGED_ON':
                self.__setConnectionStatus(CONNECTION_STATUS.disconnected)
        elif stage == 2:
            self.__setConnectionStatus(CONNECTION_STATUS.connected)
            self.onConnected()
        elif stage == 6:
            self.__setConnectionStatus(CONNECTION_STATUS.disconnected)
            self.onDisconnected()
        else:
            LOG_UNEXPECTED('stage:%d, status:%s, serverMsg:%s' % (stage, status, serverMsg))
        return

    def __setConnectionStatus(self, status):
        self.__connectionStatus = status
        return

    def isDisconnected(self):
        return self.__connectionStatus != CONNECTION_STATUS.connected

    def isUpdateClientSoftwareNeeded(self):
        return self.__rawStatus in ('LOGIN_BAD_PROTOCOL_VERSION', 'LOGIN_REJECTED_BAD_DIGEST')


def _getClientUpdateUrl():
    import ResMgr, Settings
    updateUrl = Settings.g_instance.scriptConfig.readString(Settings.KEY_UPDATE_URL)
    return updateUrl


connectionManager = ConnectionManager.instance()
return

# okay decompiling c:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\ConnectionManager.pyc
