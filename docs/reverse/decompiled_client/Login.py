# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/gui/Scaleform/Login.py
# Compiled at: 2011-05-26 15:49:26
import BigWorld, ResMgr, Settings, MusicController
from ConnectionManager import _getClientUpdateUrl, connectionManager
from debug_utils import LOG_CURRENT_EXCEPTION, LOG_DEBUG, LOG_WARNING, LOG_ERROR
from helpers import i18n
from helpers.obfuscators import PasswordObfuscator
from helpers.links import openRegistrationWebsite, openMigrationWebsite
from helpers.time_utils import makeLocalServerTime
from gui import VERSION_FILE_PATH
from gui.Scaleform.Disconnect import Disconnect
from gui.Scaleform.EULA import EULAInterface
from gui.Scaleform.Waiting import Waiting
from constants import IS_DEVELOPMENT
from external_strings_utils import isAccountLoginValid, isPasswordValid, _LOGIN_NAME_MIN_LENGTH
from gui.Scaleform.windows import UIInterface
import random

class LoginAppList(list):

    def __init__(self, *args):
        list.__init__(self, *args)
        self.cursor = 0
        self.primary = self[0] if len(self) > 0 else None
        self.__lock = False
        random.shuffle(self)
        return

    def end(self):
        return self.cursor >= len(self)

    def suspend(self):
        if self.cursor > 0 and not self.__lock:
            self.cursor -= 1
        self.__lock = True
        return

    def resume(self):
        self.__lock = False
        return

    def next(self):
        value = self[self.cursor]
        if not self.__lock:
            self.cursor += 1
        return value


class Login(UIInterface):
    __APPLICATION_CLOSE_DELAY_DEFAULT = 15

    def __init__(self):
        self.__user = ''
        self.__host = ''
        self.__rememberPwd = False
        self.__predefinedServers = {}
        self.__publicKeys = {}
        self.__loginApps = {}
        self.__closeCallbackId = None
        self.__eula = EULAInterface()
        UIInterface.__init__(self)
        return

    def populateUI(self, proxy):
        UIInterface.populateUI(self, proxy)
        self.uiHolder.movie.backgroundAlpha = 1.0
        self.uiHolder.addExternalCallbacks({'login.Login': (self.onLogin), 
           'login.Register': (self.onRegister), 
           'login.SetRememberPassword': (self.onSetRememberPassword), 
           'login.ExitFromAutoLogin': (self.onExitFromAutoLogin)})
        self.__loadUserConfig()
        self.__loadPredefinedServers(Settings.g_instance.scriptConfig['login'])
        connectionManager.connectionStatusCallbacks += self.__handleConnectionStatus
        connectionManager.onConnected += self.__onConnected
        connectionManager.searchServersCallbacks += self.__serversFind
        connectionManager.startSearchServers()
        connectionManager.onDisconnected -= Disconnect.show
        Disconnect.hide()
        self.setOptions(self.__predefinedServers.items())
        self.__loadVersion()
        Waiting.hide('loadPage')
        Waiting.close()
        MusicController.g_musicController.stopAmbient()
        MusicController.g_musicController.play(MusicController.MUSIC_EVENT_LOGIN)
        self.__eula.populateUI(proxy)
        self.uiHolder.call('login.ShowLicense', [
         self.__eula.isShowLicense()])
        return

    def dispossessUI(self):
        connectionManager.connectionStatusCallbacks -= self.__handleConnectionStatus
        connectionManager.onConnected -= self.__onConnected
        connectionManager.stopSearchServers()
        connectionManager.searchServersCallbacks -= self.__serversFind
        connectionManager.onDisconnected += Disconnect.show
        self.uiHolder.removeExternalCallbacks('login.Login', 'login.Register', 'login.SetRememberPassword', 'login.ExitFromAutoLogin')
        self.__eula.dispossessUI()
        UIInterface.dispossessUI(self)
        return

    def __loadVersion(self):
        sec = ResMgr.openSection(VERSION_FILE_PATH)
        version = i18n.makeString(sec.readString('appname')) + ' ' + sec.readString('version')
        self.call('Login.SetVersion', [version])
        return

    def __loadUserConfig(self):
        ds = Settings.g_instance.userPrefs[Settings.KEY_LOGIN_INFO]
        password = ''
        if ds:
            self.__user = ds.readString('user')
            self.__host = ds.readString('host')
            password = ds.readString('password')
            if len(password) > 0 and not ds.has_key('rememberPwd'):
                self.__rememberPwd = True
            else:
                self.__rememberPwd = ds.readBool('rememberPwd', False)
            if self.__rememberPwd:
                password = PasswordObfuscator().unobfuscate(password)
        self.call('login.setDefaultValues', [self.__user, password, self.__rememberPwd])
        return

    def __saveUserConfig(self, user, password, rememberPwd, host):
        up = Settings.g_instance.userPrefs
        if up.has_key(Settings.KEY_LOGIN_INFO):
            li = up[Settings.KEY_LOGIN_INFO]
        else:
            li = up.write(Settings.KEY_LOGIN_INFO, '')
        li.writeString('user', user)
        li.writeString('host', host)
        li.writeBool('rememberPwd', rememberPwd)
        li.writeString('password', PasswordObfuscator().obfuscate(password) if rememberPwd else '')
        Settings.g_instance.save()
        return

    def __loadPredefinedServers(self, dataSection):
        if dataSection:
            for (name, host) in dataSection.items():
                name = key_path = None
                if host.has_key('name'):
                    name = host.readString('name')
                apps = LoginAppList(host.readStrings('url'))
                if host.has_key('public_key_path'):
                    key_path = host.readString('public_key_path')
                primaryApp = apps.primary
                if primaryApp is not None:
                    if name is not None:
                        self.__predefinedServers[primaryApp] = name
                    if len(apps) > 1:
                        self.__loginApps[primaryApp] = apps
                        LOG_DEBUG('init LoginApp list ', name if name is not None else primaryApp, apps)
                    if key_path is not None:
                        self.__publicKeys[primaryApp] = key_path

        return

    def __serversFind(self, servers=None):
        list = self.__predefinedServers.items()
        if servers is not None:
            for (name, key) in servers:
                if key not in self.__predefinedServers.keys():
                    list.append((key, name))

        self.setOptions(list)
        return

    def __handleConnectionStatus(self, stage, status, serverMsg):
        if stage == 1 and status != 'LOGGED_ON':
            handlerFunc = self.__logOnFailedHandlers.get(status, self.__logOnFailedDefaultHandler)
            if status != 'LOGIN_REJECTED_LOGIN_QUEUE':
                self.__clearAutoLoginTimer()
            if status != 'LOGIN_REJECTED_RATE_LIMITED':
                self.__resetLgTimeout()
            try:
                getattr(self, handlerFunc)(status, serverMsg)
            except:
                LOG_ERROR('Handle logon status error: status = %r, message = %r' % (
                 status, serverMsg))
                LOG_CURRENT_EXCEPTION()
                Waiting.hide('login')

            if connectionManager.isUpdateClientSoftwareNeeded():
                self.__handleUpdateClientSoftwareNeeded()
            else:
                connectionManager.disconnect()
        elif stage == 6:
            self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/disconnected')))
            connectionManager.disconnect()
        return

    def __onConnected(self):
        Waiting.hide('login')
        Waiting.show('enter')
        LOG_DEBUG('onConnected')
        return

    def __handleUpdateClientSoftwareNeeded(self):
        updateUrl = _getClientUpdateUrl()
        text = i18n.convert(i18n.makeString('#menu:login/updateURLAvaialbleAt')) % updateUrl
        self.__setStatus(text)
        LOG_WARNING('Client software update needed. Update URL: %s' % updateUrl)
        if not IS_DEVELOPMENT:
            self.__closeCallbackId = BigWorld.callback(self.__getApplicationCloseDelay(), BigWorld.quit)
            try:
                BigWorld.wg_openWebBrowser(updateUrl)
            except Exception:
                LOG_CURRENT_EXCEPTION()

        return

    def __handleMigrationNeeded(self):
        if not IS_DEVELOPMENT:
            self.__closeCallbackId = BigWorld.callback(self.__getApplicationCloseDelay(), BigWorld.quit)
            try:
                openMigrationWebsite(self.__user)
            except Exception:
                LOG_CURRENT_EXCEPTION()

        return

    def __getApplicationCloseDelay(self):
        prefs = Settings.g_instance.userPrefs
        if prefs is None:
            delay = Login.__APPLICATION_CLOSE_DELAY_DEFAULT
        elif not prefs.has_key(Settings.APPLICATION_CLOSE_DELAY):
            prefs.writeInt(Settings.APPLICATION_CLOSE_DELAY, Login.__APPLICATION_CLOSE_DELAY_DEFAULT)
        delay = prefs.readInt(Settings.APPLICATION_CLOSE_DELAY)
        return delay

    def setOptions(self, optionsList):
        options = [
         0]
        for (i, (key, name)) in enumerate(optionsList):
            if key == self.__host:
                options[0] = i
            options.append(name)
            options.append(key)

        self.call('login.setServersList', options)
        return

    def __setStatus(self, status):
        self.call('login.setErrorMessage', [status])
        Waiting.hide('login')
        return

    __isAutoLoginTimerSet = False
    __isAutoLoginShow = False
    __autoLoginTimerID = None

    def __setAutoLoginTimer(self, time):
        if self.__isAutoLoginTimerSet:
            return
        self.__isAutoLoginTimerSet = True
        LOG_DEBUG('__setAutoLoginTimer', time)
        self.call('login.setAutoLogin')
        self.__isAutoLoginShow = True
        if time > 0:
            self.__autoLoginTimerID = BigWorld.callback(time, self.__doAutoLogin)
        else:
            self.__doAutoLogin()
        return

    def __clearAutoLoginTimer(self, clearInFlash=True):
        if self.__isAutoLoginTimerSet:
            LOG_DEBUG('__clearAutoLoginTimer')
            if self.__autoLoginTimerID is not None:
                BigWorld.cancelCallback(self.__autoLoginTimerID)
                self.__autoLoginTimerID = None
            self.__isAutoLoginTimerSet = False
        if self.__isAutoLoginShow:
            apps = self.__loginApps.get(self.__host)
            if apps is not None:
                apps.resume()
            self.__minOrderInQueue = 18446744073709551615L
            if clearInFlash:
                self.call('login.clearAutoLogin')
            self.__isAutoLoginShow = False
        return

    def __doAutoLogin(self):
        LOG_DEBUG('__doAutoLogin')
        self.__isAutoLoginTimerSet = False
        self.__autoLoginTimerID = None
        self.call('login.doAutoLogin')
        return

    __lg_Timeout = 0
    __lg_maxTimeout = 20
    __lg_increment = 5

    def __getLgNextTimeout(self):
        self.__lg_Timeout = min(self.__lg_maxTimeout, self.__lg_Timeout + self.__lg_increment)
        return self.__lg_Timeout

    def __resetLgTimeout(self):
        self.__lg_Timeout = 0
        return

    __logOnFailedHandlers = {'LOGIN_REJECTED_BAN': 'handleLoginRejectedBan', 
       'LOGIN_REJECTED_LOGIN_QUEUE': 'handleLoginRejectedQueue', 
       'LOGIN_CUSTOM_DEFINED_ERROR': 'handleLoginRejectedBan', 
       'LOGIN_REJECTED_RATE_LIMITED': 'handleLoginRejectedRateLimited', 
       'LOGIN_REJECTED_LOGINS_NOT_ALLOWED': 'handleLoginAppFailed', 
       'LOGIN_REJECTED_BASEAPP_TIMEOUT': 'handleLoginAppFailed', 
       'CONNECTION_FAILED': 'handleLoginAppFailed', 
       'DNS_LOOKUP_FAILED': 'handleLoginAppFailed'}
    __logOnFailedDefaultHandler = 'handleLogOnFailed'
    __minOrderInQueue = 18446744073709551615L

    def handleLogOnFailed(self, status, message):
        errorMessage = i18n.makeString('#menu:login/status/' + status)
        self.__setStatus(errorMessage)
        return

    def handleLoginRejectedBan(self, status, message):
        if message.find(';') != -1:
            (expiryTime, reason) = message.split(';', 1)
            expiryTime = int(expiryTime)
        else:
            self.handleLoginCustomDefinedError(status, message)
            return
        if reason == '#ban_reason:china_migration':
            self.__handleMigrationNeeded()
        if reason.startswith('#'):
            reason = i18n.makeString(reason)
        if expiryTime > 0:
            expiryTime = makeLocalServerTime(expiryTime)
            expiryTime = BigWorld.wg_getLongDateFormat(expiryTime) + ' ' + BigWorld.wg_getLongTimeFormat(expiryTime)
            errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_BAN', time=expiryTime, reason=reason)
        else:
            errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_BAN_UNLIMITED', reason=reason)
        self.__setStatus(errorMessage)
        return

    def handleLoginRejectedQueue(self, status, message):
        orderInQueue = int(message) + 1
        self.__minOrderInQueue = min(orderInQueue, self.__minOrderInQueue)
        errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_LOGIN_QUEUE', self.__minOrderInQueue)
        self.__setStatus(errorMessage)
        apps = self.__loginApps.get(self.__host)
        if apps is not None:
            apps.suspend()
        self.__setAutoLoginTimer(5)
        return

    def handleLoginCustomDefinedError(self, status, message):
        errorMessage = i18n.makeString('#menu:login/status/LOGIN_CUSTOM_DEFINED_ERROR', message)
        self.__setStatus(errorMessage)
        return

    def handleLoginRejectedRateLimited(self, status, message):
        errorMessage = i18n.makeString('#menu:login/status/LOGIN_REJECTED_RATE_LIMITED')
        self.__setStatus(errorMessage)
        apps = self.__loginApps.get(self.__host)
        if apps is not None and apps.end():
            apps.cursor = 0
        self.__setAutoLoginTimer(self.__getLgNextTimeout())
        return

    def __loginToNextLoginApp(self):
        apps = self.__loginApps.get(self.__host)
        result = False
        if apps is not None:
            result = not apps.end()
            if result:
                Waiting.hide('login')
                self.__setAutoLoginTimer(0)
            else:
                apps.cursor = 0
        return result

    def handleLoginAppFailed(self, status, message):
        if not self.__loginToNextLoginApp():
            self.handleLogOnFailed(status, message)
        return

    def onLogin(self, id, user, password, host):
        if self.__closeCallbackId:
            BigWorld.cancelCallback(self.__closeCallbackId)
            self.__closeCallbackId = None
        user = user.lower().strip()
        if len(user) <= _LOGIN_NAME_MIN_LENGTH:
            self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/invalid_login_length')))
            return
        else:
            if not isAccountLoginValid(user) and not IS_DEVELOPMENT:
                self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/invalid_login')))
                return
            password = password.strip()
            if not isPasswordValid(password) and not IS_DEVELOPMENT:
                self.__setStatus(i18n.convert(i18n.makeString('#menu:login/status/invalid_password')))
                return
            Waiting.show('login')
            self.__host = host
            self.__user = user
            if self.__loginApps.has_key(host):
                host = self.__loginApps[host].next()
                LOG_DEBUG('Gets next LoginApp url:', host)
            self.__saveUserConfig(user, password, self.__rememberPwd, host)
            publicKey = self.__publicKeys.get(host, None)
            connectionManager.connect(host, user, password, publicKey)
            return

    def onRegister(self, callbackID):
        openRegistrationWebsite()
        return

    def onSetRememberPassword(self, requestId, remember):
        self.__rememberPwd = bool(remember)
        return

    def onExitFromAutoLogin(self, *args):
        self.__clearAutoLoginTimer(clearInFlash=False)
        self.__resetLgTimeout()
        return


return

# okay decompiling c:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\gui\Scaleform\Login.pyc
