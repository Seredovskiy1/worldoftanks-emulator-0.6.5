from encodings import utf_8
import os
import gettext
import constants
import BigWorld
from debug_utils import LOG_WARNING, LOG_CURRENT_EXCEPTION

g_translators = {}

def convert(utf8String):
    try:
        return utf_8.decode(utf8String)[0]
    except Exception:
        LOG_CURRENT_EXCEPTION()
        LOG_WARNING('Wrong UTF8 string', utf8String)
        return utf_8.decode('----')[0]

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
                try:
                    resolved = convert(BigWorld.wg_resolveFileName('text'))
                    path = os.path.dirname(resolved.replace('\\', '/').rstrip('/'))
                    translator = gettext.translation(moName, path, languages=['text'])
                except IOError:
                    translator = gettext.NullTranslations()
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
