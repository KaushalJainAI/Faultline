import warnings
def old_func():
    warnings.warn("old_func is deprecated", DeprecationWarning, stacklevel=2)
old_func()
