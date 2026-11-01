"""
This is the application entry. This is where we create and run the
Application.
"""
import os
import sys


def override_sys_path():
    """
    Prepend extlibs folder to sys.path.
    """
    import open_cobol_ide
    extlibs_pth = os.path.join(
        os.path.dirname(open_cobol_ide.__file__),
        'extlibs')
    sys.path.insert(0, extlibs_pth)
    os.environ['OCIDE_EXTLIBS_PATH'] = extlibs_pth
    import pyqode.core


def setup_portable_settings():
    """
    Tells the whole application (and pyqode, which runs in the same process
    and in the backend) to keep its settings in <app>/data (.ini files)
    instead of the Windows registry.
    """
    from open_cobol_ide import system
    os.environ['OCIDE_DATA_DIR'] = system.data_dir()


def main():
    """
    Application entry point.
    """
    dev_mode = os.environ.get('OCIDE_DEV_MODE')
    if not hasattr(sys, 'frozen') and dev_mode is None:
        override_sys_path()
    from pyqode.qt import QtGui
    from open_cobol_ide import system
    from open_cobol_ide.app import Application
    from open_cobol_ide.settings import Settings
    setup_portable_settings()
    app = Application()
    if system.linux:
        QtGui.QIcon.setThemeName(Settings().icon_theme)
    ret_code = app.run()
    app.close()
    return ret_code
