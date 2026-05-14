import sys
import types

from server_core import emulator_impl as _impl


class _EmulatorFacade(types.ModuleType):
    def __getattr__(self, name):
        return getattr(_impl, name)

    def __setattr__(self, name, value):
        if name in {"_impl", "_EmulatorFacade", "__class__"}:
            return super().__setattr__(name, value)
        setattr(_impl, name, value)

    def __delattr__(self, name):
        if hasattr(_impl, name):
            delattr(_impl, name)
            return
        super().__delattr__(name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(dir(_impl)))


sys.modules[__name__].__class__ = _EmulatorFacade
__all__ = [name for name in dir(_impl) if not name.startswith("_")]


if __name__ == "__main__":
    _impl.main()
