"""
This module contains the ide application. This is where we glue the
various managers and gui parts together.

"""
import traceback
import argparse
import logging
import mimetypes
import os
import sys
from pyqode.qt import QtCore, QtWidgets, QT_API, PYQT5_API, PYSIDE_API

from open_cobol_ide.translation import tr
from open_cobol_ide import __version__, logger, system
from open_cobol_ide.controllers import (
    CobolController, EditController, FileController, HelpController,
    HomeController, ViewController)
from open_cobol_ide.controllers.cobol import CompilationThread
from open_cobol_ide.compilers import check_compiler, CompilerNotFound, \
    GnuCobolCompiler
from open_cobol_ide.settings import Settings
from open_cobol_ide.view.main_window import MainWindow
from open_cobol_ide.view.editors import PythonCodeEdit
from open_cobol_ide.view.dialogs.about import DlgAbout


def _logger():
    return logging.getLogger(__name__)


_original_env = os.environ.copy()




class Application(QtCore.QObject):
    """
    Sets up the Qt Application, the main window and the various controllers.

    The application class contains references to the main window user interface
    and to the various controllers so that they can collaborate each others.
    """
    def apply_mimetypes_preferences(self):
        for ext in Settings().all_extensions:
            mimetypes.add_type('text/x-cobol', ext)
            mimetypes.add_type('text/x-cobol', ext.upper())
        # le registre Windows peut donner un autre type aux .py
        for ext in PythonCodeEdit.extensions:
            mimetypes.add_type('text/x-python', ext)

    def __init__(self, parse_args=True):
        super().__init__()
        if system.darwin:
            Application._osx_init()
        self.app = QtWidgets.QApplication(sys.argv)
        from open_cobol_ide import translation
        translation.install(self.app)

        self._reported_tracebacks = []
        sys.excepthook = self._except_hook
        # if hasattr(sys, 'frozen') and sys.platform == 'win32':
        #     sys.stdout = open(os.path.join(system.get_cache_directory(),
        #                                    'ocide_stdout.log'), 'w')
        #     sys.stderr = open(os.path.join(system.get_cache_directory(),
        #                                    'ocide_stderr.log'), 'w')
        if parse_args and not system.darwin:
            files = self.handle_command_line_args()
        else:
            files = []
        _logger().info('files to open: %r' % files)
        self.name = 'OpenCobolIDE'
        self.version = __version__
        self.title = '%s %s' % (self.name, self.version)

        self.apply_mimetypes_preferences()

        self.win = MainWindow()
        self.win.setWindowTitle(self.title)
        self.file = FileController(self)
        self.view = ViewController(self)
        self.home = HomeController(self)
        self.edit = EditController(self)
        self.cobol = CobolController(self)
        self.help = HelpController(self)
        self.win.app = self

        self.view.show_perspective(Settings().perspective)

        self.update_app_style()

        try:
            check_compiler()
        except CompilerNotFound:
            msg = tr('Failed to find a working GnuCOBOL compiler!\n' \
                "The IDE will continue to work but you won't be able to " \
                'compile...')
            if system.windows:
                msg += tr('\n\nTip: Ensure that there is no additional ' \
                    'installation of MinGW in %s:\\MinGW') % sys.executable[0]
            QtWidgets.QMessageBox.warning(
                self.win, tr('COBOL compiler not found or not working'), msg)
        else:
            _logger().info('GnuCOBOL version: %s',
                           GnuCobolCompiler.get_version(include_all=False))
        self._files = files

    def restart(self):
        """
        Restarts the IDE.
        """
        if hasattr(sys, 'frozen'):
            QtCore.QProcess.startDetached(sys.executable)
        else:
            QtCore.QProcess.startDetached(sys.executable, sys.argv)
        sys.exit(0)

    def close(self):
        self.view = None
        self.cobol = None
        self.edit = None
        self.file = None
        self.win = None
        self.home = None
        self.help = None

    def update_app_style(self):
        if Settings().dark_style:
            try:
                import qdarkstyle
            except ImportError:
                Settings().dark_style = False
            else:
                qt_api = os.environ[QT_API]
                if qt_api in PYQT5_API:
                    qss = qdarkstyle.load_stylesheet_pyqt5()
                else:
                    qss = qdarkstyle.load_stylesheet(qt_api in PYSIDE_API)
                self.app.setStyleSheet(qss)
                return
        elif system.windows and QtCore.QSysInfo.windowsVersion() == QtCore.QSysInfo.WV_WINDOWS10:
            self.app.setStyleSheet('QToolBar { background-color: white;};')
        else:
            self.app.setStyleSheet('')

    def run(self):
        """
        Run the Qt main loop.
        """
        if Settings().fullscreen:
            self.win.showFullScreen()
        else:
            self.win.show()
            self.view.restore_state()
            self.view.show_home_page()
            if self._files:
                # open specified files
                for f in self._files:
                    self.file.open_file(f)
        return self.app.exec_()

    @staticmethod
    def _osx_init():
        """
        Mac OSX specific initialisation, adds missing path to the PATH
        environment variable (see github issue #40).

        """
        #
        paths = ['/bin', '/sbin', '/usr/bin', '/usr/sbin', '/usr/local/bin',
                 '/usr/local/sbin', '/opt/bin', '/opt/sbin', '/opt/local/bin',
                 '/opt/local/sbin']
        os.environ['PATH'] = ':'.join(paths)

    def exit(self):
        """
        Closes all top level windows and quits the application (without
        prompting the user, if you need to prompt the user, use
        Application.file.quit())
        """
        self.app.closeAllWindows()
        self.close()

    def handle_command_line_args(self):
        args = self.parse_args()
        files = args.files

        # setup logger
        debug = args.verbose or Settings().verbose
        logger.setup_logging(__version__, level=Settings().log_level)

        # show runtime env
        if args.runtime_env:
            print('OpenCobolIDE %s' % __version__)
            for k, v in sorted(DlgAbout.get_runtime_env().items(),
                               key=lambda x: x[0]):
                print('%s %s' % (k, v))
            sys.exit(0)

        # show cobc runtime env
        if args.cobc_runtime_env:
            print(DlgAbout.get_cobc_runtime_env())
            sys.exit(0)

        # import preferences
        if args.conf:
            try:
                Settings().import_from_json(args.conf)
            except (ValueError, IOError, OSError):
                _logger().exception('failed to restore preferences from %r',
                                    args.conf)
            else:
                _logger().info('preferences imported from %r', args.conf)

        # compile specified files
        if args.compile:
            thread = CompilationThread()
            for path in files:
                if os.path.exists(path):
                    CobolController.clean_file(path)
                    thread.file_path = path
                    thread.run()
                else:
                    print('cannot compile %r, file not found')
            sys.exit(0)

        return files

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description='Simple and lightweight COBOL IDE.')
        parser.add_argument('files', type=str, nargs='*',
                            help='List of files to open, if any')
        parser.add_argument('--verbose', dest='verbose', action='store_true',
                            help='Verbose mode will enable debug and info '
                                 'messages to be shown in the application log')
        parser.add_argument(
            '--runtime-env', dest='runtime_env', action='store_true',
            help='Display the application runtime environment.')

        parser.add_argument(
            '--cobc-runtime-env', dest='cobc_runtime_env',
            action='store_true',
            help='Display the compiler runtime environment.')

        parser.add_argument(
            '--compile', dest='compile',
            action='store_true',
            help='Compile the specified files and exits.')

        parser.add_argument(
            '--conf', dest='conf',
            help='Specify a configuration file (previously exported from '
            'within the IDE (File->Export preferences))')

        return parser.parse_args()

    def _except_hook(self, exc_type, exc_val, tb):
        tb = '\n'.join([''.join(traceback.format_tb(tb)),
                        '{0}: {1}'.format(exc_type.__name__, exc_val)])
        # exception might come from another thread, use a signal
        # so that we can be sure we will show the bug report dialog from
        # the main gui thread.
        self._report_exception_requested.emit(exc_val, tb)

    def _except_hook(self, exc_type, exc, tb):
        """
        Logs unhandled exceptions and shows them to the user.
        """
        self._report_exception(
            exc, ''.join(traceback.format_exception(exc_type, exc, tb)))

    def _report_exception(self, exc, tb):
        try:
            _logger().critical('unhandled exception:\n%s', tb)
            _tb = tb
            if isinstance(exc, UnicodeDecodeError):
                # This might be the same exception in the same file but at
                # another position in the stream
                _tb = tb.splitlines()[-4]
            if _tb in self._reported_tracebacks:
                return
            self._reported_tracebacks.append(_tb)
            title = tr('[Unhandled exception] %s') % exc.__class__.__name__
            description = tr('An unhandled exception has occured:\n\n%s') % tb
            QtWidgets.QMessageBox.critical(self.win, title, description)
        except Exception:
            _logger().exception('exception in excepthook')
