# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/gui/Scaleform/Waiting.py
# Compiled at: 2011-05-26 15:49:26
from gui.Scaleform.windows import ModalWindow
from debug_utils import LOG_DEBUG
import inspect

class _Waiting(ModalWindow):

    def __init__(self):
        ModalWindow.__init__(self, 'waiting.swf')
        self.component.position.z = 0.1
        return

    def setMessage(self, message):
        self.setMovieVariable('_root._level0.setMessage', ['#menu:waiting/%s' % message])
        return

    def __del__(self):
        return


__waiting = None

class Waiting:
    __window = None
    __waitingStack = []

    @classmethod
    def isVisible(cls):
        return cls.__window is not None

    @staticmethod
    def show(message, isSingle=False):
        if not (isSingle and message in Waiting.__waitingStack):
            Waiting.__waitingStack.append(message)
        if Waiting.__window is None:
            Waiting.__window = _Waiting()
            Waiting.__window.setMessage(message)
            Waiting.__window.active(True)
        else:
            Waiting.__window.setMessage(message)
        return

    @staticmethod
    def hide(message):
        if Waiting.__window is None:
            LOG_DEBUG('Waitin.hide without show: in %s line %d func %s' % inspect.stack()[1][1:4])
            return
        else:
            try:
                Waiting.__waitingStack.remove(message)
            except:
                LOG_DEBUG('Waitin.hide without show: ', message, inspect.stack()[1][1:4])

            if len(Waiting.__waitingStack) == 0:
                Waiting.close()
            return

    @staticmethod
    def close():
        if Waiting.__window:
            Waiting.__window.close()
            Waiting.__window = None
            Waiting.__waitingStack = []
        return


return
