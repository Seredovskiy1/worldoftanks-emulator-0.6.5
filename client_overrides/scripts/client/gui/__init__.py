import os

import sys



__path__ = [os.path.abspath(os.path.dirname(__file__))]

for p in sys.path:

    candidate = os.path.join(p, 'gui')

    if os.path.isdir(candidate) and os.path.abspath(candidate) != __path__[0]:

        __path__.append(candidate)

