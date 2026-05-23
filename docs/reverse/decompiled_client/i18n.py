# uncompyle6 version 3.9.3
# Python bytecode version base 2.6 (62161)
# Decompiled from: Python 3.9.13 (tags/v3.9.13:6de2ca5, May 17 2022, 16:36:42) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: scripts/client/helpers/i18n.py
# Compiled at: 2011-05-26 15:49:25
from encodings import utf_8
import gettext, constants, BigWorld
from debug_utils import LOG_WARNING, LOG_CURRENT_EXCEPTION
g_translators = {}

def convert(utf8String):
    try:
        return utf_8.decode(utf8String)[0]
    except Exception:
        LOG_CURRENT_EXCEPTION()
        LOG_WARNING('Wrong UTF8 string', utf8String)
        return utf_8.decode('----')[0]

    return


def makeString(key, *args, **kargs):
    global g_translators
    try:
        if not key or key[0] != '#':
            return key
        else:
            (moName, subkey) = key[1:].split(':', 1)
            if not moName or not subkey:
                return key
            translator = g_translators.get(moName)
            if translator is None:
                path = convert(BigWorld.wg_resolveFileName('')[:-1])
                translator = gettext.translation(moName, path, languages=['text'])
                g_translators[moName] = translator
            text = translator.gettext(subkey)
            if text == '?empty?':
                text = ''
            if args:
                try:
                    text = text % args
                except TypeError:
                    LOG_WARNING("Arguments do not match string read by key '%s': %s", (key, args))
                    return key

            elif kargs:
                try:
                    text = text % kargs
                except TypeError:
                    LOG_WARNING("Arguments do not match string read by key '%s': %s", (key, kargs))
                    return key

            return text
    except Exception:
        LOG_CURRENT_EXCEPTION()
        LOG_WARNING('Key string incompatible with args', key, args, kargs)
        return key

    return


return

# okay decompiling c:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\client\helpers\i18n.pyc
