import os

import sys



__path__ = [os.path.abspath(os.path.dirname(__file__))]

try:

    import gui

    for p in gui.__path__:

        candidate = os.path.join(p, 'Scaleform')

        if os.path.isdir(candidate) and os.path.abspath(candidate) != __path__[0]:

            __path__.append(candidate)

except Exception:

    pass

