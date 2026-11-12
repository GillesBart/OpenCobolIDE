"""
System utility module (get system info, platform specific path,...).
"""
import locale
import subprocess
import functools
import os
import platform
import shlex
import sys

from pyqode.qt import QtCore, QtGui


windows = platform.system() == 'Windows'
darwin = platform.system() == 'Darwin'
linux = platform.system() == 'Linux'
ubuntu = False
if linux:
    try:
        ubuntu = platform.freedesktop_os_release().get(
            'ID', '').lower() == 'ubuntu'
    except (AttributeError, OSError):
        pass


def _mkdir(func):
    @functools.wraps(func)
    def wrapper(*args, **kwds):
        ret = func(*args, **kwds)
        try:
            os.makedirs(ret)
        except OSError:
            pass
        return ret
    return wrapper


def which(cmd, mode=os.F_OK | os.X_OK, path=None,
          include_settings_path=True):
    """Given a command, mode, and a PATH string, return the path which
    conforms to the given mode on the PATH, or None if there is no such
    file.

    `mode` defaults to os.F_OK | os.X_OK. `path` defaults to the result
    of os.environ.get("PATH"), or can be overridden with a custom search
    path.

    TAKEN from the shutil module (cause it is not available on python <= 3.3)
    """
    from open_cobol_ide.settings import Settings

    # Check that a given file can be accessed with the correct mode.
    # Additionally check that `file` is not a directory, as on Windows
    # directories pass the os.access check.
    def _access_check(fn, mode):
        return (os.path.exists(fn) and os.access(fn, mode)
                and not os.path.isdir(fn))

    # If we're given a path with a directory part, look it up directly rather
    # than referring to PATH directories. This includes checking relative to the
    # current directory, e.g. ./script
    if os.path.dirname(cmd):
        if _access_check(cmd, mode):
            return cmd
        return None

    if path is None:
        path = os.environ.get("PATH", os.defpath)
    if not path:
        return None

    path = path.split(os.pathsep)
    if include_settings_path and Settings().path:
        path = Settings().path.split(os.pathsep) + path

    if sys.platform == "win32":
        # The current directory takes precedence on Windows.
        if not os.curdir in path:
            path.insert(0, os.curdir)

        # PATHEXT is necessary to check on Windows.
        pathext = os.environ.get("PATHEXT", "").split(os.pathsep)
        # See if the given file matches any of the expected path extensions.
        # This will allow us to short circuit when given "python.exe".
        # If it does match, only test that one, otherwise we have to try
        # others.
        if any(cmd.lower().endswith(ext.lower()) for ext in pathext):
            files = [cmd]
        else:
            files = [cmd + ext for ext in pathext]
    else:
        # On other platforms you don't have things like PATHEXT to tell you
        # what file suffixes are executable, so just pass on cmd as-is.
        files = [cmd]

    seen = set()

    for dir in path:
        normdir = os.path.normcase(dir)
        if not normdir in seen:
            seen.add(normdir)
            for thefile in files:
                name = os.path.join(dir, thefile)
                if _access_check(name, mode):
                    return name
    return None


@_mkdir
def get_cache_directory():
    """
    Gets the cache directory (log file and temporary files created by the
    linter). Portable fork: always next to the application.
    :return: cache directory.
    """
    return os.path.join(data_dir(), 'cache')


def icon_themes():
    themes = []
    for path in QtGui.QIcon.themeSearchPaths():
        try:
            dirs = os.listdir(path)
        except OSError:
            pass
        else:
            for d in dirs:
                pth = os.path.join(path, d)
                if os.path.isfile(pth):
                    continue
                try:
                    files = os.listdir(pth)
                except OSError:
                    files = []
                if 'cursors' not in files and 'index.theme' in files:
                    themes.append(d)
    return sorted(list(set(themes)))


# ---------------------------------------------------------------------------
# Portable mode helpers
# ---------------------------------------------------------------------------
APP_TAG = '@APP@/'


def app_dir():
    """
    Root folder of the application: the folder of the executable when frozen,
    the repository root when running from sources.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def documents_dir():
    """
    Dossier Documents de l'utilisateur (repli : son dossier personnel).
    """
    try:
        from pyqode.qt import QtCore
        path = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.DocumentsLocation)
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    except Exception:
        pass
    return os.path.expanduser('~')


def board_cache_base():
    """
    Dossier temporaire (propre à l'utilisateur) qui contient les copies de
    travail des fichiers des cartes MicroPython : un sous-dossier par
    instance de l'IDE (numéro du processus).
    """
    import getpass
    import tempfile
    try:
        user = getpass.getuser()
    except Exception:
        user = 'user'
    return os.path.join(tempfile.gettempdir(),
                        'CobraIDE-boards-%s' % user)


def board_cache_dir():
    """Copies de travail de CETTE instance : effacées à la fermeture."""
    return os.path.join(board_cache_base(), str(os.getpid()))


def _process_alive(pid):
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259                     # STILL_ACTIVE
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True         # le processus existe (autre utilisateur)
    except (OSError, ValueError):
        return False


def clean_stale_board_caches():
    """
    Au démarrage : efface les copies de travail laissées par une session
    interrompue (plantage, arrêt brutal), et celles des anciennes versions.
    Les dossiers d'une autre instance de l'IDE encore ouverte sont gardés.
    """
    import shutil
    base = board_cache_base()
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for name in entries:
        path = os.path.join(base, name)
        if name.isdigit() and int(name) != os.getpid() and \
                _process_alive(int(name)):
            continue
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def data_dir():
    """
    Folder holding everything the IDE writes (settings .ini, cache, logs).
    Lives next to the application so that the whole folder can be moved.
    """
    path = os.path.join(app_dir(), 'data')
    try:
        os.makedirs(path)
    except OSError:
        pass
    return path


#: nom du fichier de préférences (data/CobraIDE.ini)
SETTINGS_NAME = 'CobraIDE'
#: ancien nom (OpenCobolIDE 4.x) : repris une fois si CobraIDE.ini n'existe pas
LEGACY_SETTINGS_NAME = 'OpenCobolIDE4'


def _migrate_legacy_settings(name):
    """
    Au premier lancement de CobraIDE, copie data/OpenCobolIDE4.ini vers
    data/CobraIDE.ini pour conserver préférences et fichiers récents.
    L'ancien fichier n'est pas touché.
    """
    if name != SETTINGS_NAME:
        return
    new = os.path.join(data_dir(), SETTINGS_NAME + '.ini')
    old = os.path.join(data_dir(), LEGACY_SETTINGS_NAME + '.ini')
    if not os.path.exists(new) and os.path.isfile(old):
        try:
            import shutil
            shutil.copyfile(old, new)
        except OSError:
            pass


def make_settings(name):
    """
    Creates a QSettings backed by <app>/data/<name>.ini (never the registry).
    """
    _migrate_legacy_settings(name)
    return QtCore.QSettings(os.path.join(data_dir(), name + '.ini'),
                            QtCore.QSettings.IniFormat)


def to_portable(path):
    """
    Returns ``@APP@/relative/path`` if *path* is located inside app_dir(),
    otherwise *path* unchanged.
    """
    if not path:
        return path
    path = str(path)
    if path.startswith(APP_TAG) or not os.path.isabs(path):
        return path
    root = os.path.abspath(app_dir())
    full = os.path.abspath(path)
    try:
        if os.path.normcase(os.path.commonpath([root, full])) == \
                os.path.normcase(root):
            return APP_TAG + os.path.relpath(full, root).replace('\\', '/')
    except ValueError:
        pass  # different drives
    return path


def from_portable(path):
    """
    Inverse of to_portable: resolves ``@APP@/...`` against app_dir().
    """
    if path and str(path).startswith(APP_TAG):
        return os.path.normpath(
            os.path.join(app_dir(), str(path)[len(APP_TAG):]))
    return path


def to_portable_list(value, sep=os.pathsep):
    return sep.join(to_portable(p) for p in str(value).split(sep) if p)


def from_portable_list(value, sep=os.pathsep):
    return sep.join(from_portable(p) for p in str(value).split(sep) if p)


def normpath(path):
    if path:
        return os.path.normpath(path)
    return path


def shell_split(string):
    return shlex.split(string, posix=False)


def get_system_infos():
    from open_cobol_ide import __version__
    from open_cobol_ide.compilers import GnuCobolCompiler
    from pyqode.qt import QtCore
    import pyqode.core
    import pyqode.cobol
    import pygments

    try:
        import qdarkstyle
    except ImportError:
        qdarkstyle_version = 'Not installed'
    else:
        qdarkstyle_version = qdarkstyle.__version__

    def get_linux_distro():
        try:
            out = str(subprocess.check_output(['lsb_release', '-i']),
                      locale.getpreferredencoding())
        except OSError:
            distro = platform.linux_distribution()[0]
            if not distro:
                distro = 'linux distribution not found'
        else:
            distro = out.split(':')[1].strip()
        return distro

    system_info = platform.system()
    if 'linux' in sys.platform.lower():
        system_info = get_linux_distro()
    elif 'darwin' in sys.platform.lower():
        system_info = 'Mac OS X %s' % platform.mac_ver()[0]
    return '\n'.join([
        'Operating System: %s' % system_info,
        'CobraIDE: %s' % __version__,
        'GnuCOBOL: %s' % GnuCobolCompiler().get_version(
            include_all=False),
        'Python: %s (%dbits)' % (platform.python_version(), 64
                                 if sys.maxsize > 2**32 else 32),
        'Qt: %s' % QtCore.QT_VERSION_STR,
        'PyQt: %s' % QtCore.PYQT_VERSION_STR,
        'pyqode.core: %s' % pyqode.core.__version__,
        'pyqode.cobol: %s' % pyqode.cobol.__version__,
        'pyqode.qt: %s' % pyqode.qt.__version__,
        'pygments: %s' % pygments.__version__,
        'QDarkStyle: %s' % qdarkstyle_version
    ])
