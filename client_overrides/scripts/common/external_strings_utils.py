import sys

import os



orig_path = list(sys.path)

if orig_path:

    sys.path.pop(0)



import external_strings_utils as _orig



sys.path = orig_path



globals().update(_orig.__dict__)



def isPasswordValid(text):

    return len(text) >= 6 and len(text) <= 100

