"""
Contrôleur du menu Python : exécution du fichier courant avec l'interpréteur
choisi (ou trouvé dans le PATH), sortie dans la console de l'IDE.
"""
import logging
import os
import subprocess
import sys

from pyqode.core.api import TextHelper
from pyqode.qt import QtWidgets

from open_cobol_ide import system
from open_cobol_ide.settings import Settings
from open_cobol_ide.translation import tr
from open_cobol_ide.view.editors import PythonCodeEdit
from .base import Controller
from .cobol import LOG_PAGE_RUN


def _logger():
    return logging.getLogger(__name__)


def split_arguments(text):
    """
    Découpe une ligne d'arguments en respectant les guillemets, sans
    interpréter les antislashs (chemins Windows).
    """
    args, current, quote = [], '', None
    started = False
    for c in text:
        if quote:
            if c == quote:
                quote = None
            else:
                current += c
        elif c in '"\'':
            quote = c
            started = True
        elif c.isspace():
            if current or started:
                args.append(current)
            current, started = '', False
        else:
            current += c
    if current or started:
        args.append(current)
    return args


def no_window_kwargs():
    """
    Arguments de subprocess pour ne pas faire clignoter une fenêtre console
    (l'IDE est une application graphique) lors d'un appel de courte durée.
    """
    if sys.platform == 'win32':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


def find_interpreter():
    """
    Interpréteur Python à utiliser : celui des préférences, sinon le premier
    trouvé dans le PATH (hors alias du Windows Store), sinon, en mode source,
    celui qui fait tourner l'IDE. Renvoie None si rien n'est trouvé.
    """
    path = Settings().python_interpreter
    if path and os.path.isfile(path):
        return path
    for name in ('python', 'python3', 'py'):
        found = system.which(name)
        if found and 'WindowsApps' not in found:
            return found
    if not hasattr(sys, 'frozen') and os.path.isfile(sys.executable):
        return sys.executable
    return None


def child_environment():
    """
    Environnement du script lancé : celui de l'IDE, sans ce qui sert à faire
    tourner l'IDE lui-même (PYTHONHOME, entrées de PYTHONPATH vers
    open_cobol_ide/extlibs) et avec une sortie en UTF-8.
    """
    env = {'PYTHONIOENCODING': 'utf-8', 'PYTHONHOME': ''}
    pythonpath = os.environ.get('PYTHONPATH', '')
    if pythonpath:
        kept = [p for p in pythonpath.split(os.pathsep)
                if p and 'open_cobol_ide' not in p.replace('\\', '/')
                and os.path.abspath(p) != os.path.abspath(system.app_dir())]
        env['PYTHONPATH'] = os.pathsep.join(kept)
    return env


class PythonController(Controller):
    """
    Contrôle le menu Python.
    """
    def __init__(self, app):
        super().__init__(app)
        ui = self.ui
        self.menu = QtWidgets.QMenu(tr('&Python'), self.main_window)
        self.menu.addAction(ui.actionRun)
        self.menu.addAction(ui.actionCancel)
        # bouton Stop à côté du bouton Exécuter
        ui.toolBarCOBOL.addAction(ui.actionCancel)
        self.menu.addSeparator()
        self.actionInterpreter = QtWidgets.QAction(
            tr('Python &interpreter...'), self.main_window)
        self.actionInterpreter.triggered.connect(self.choose_interpreter)
        self.menu.addAction(self.actionInterpreter)
        self.actionArguments = QtWidgets.QAction(
            tr('Script &arguments...'), self.main_window)
        self.actionArguments.triggered.connect(self.edit_arguments)
        self.menu.addAction(self.actionArguments)
        self.menu.aboutToShow.connect(self._update_menu)
        # juste avant le menu d'aide
        ui.menuBar.insertMenu(ui.menu.menuAction(), self.menu)
        ui.consoleOutput.open_file_requested.connect(self._open_file_at)

    @staticmethod
    def is_python(editor):
        return isinstance(editor, PythonCodeEdit)

    def _update_menu(self):
        interpreter = find_interpreter()
        self.actionInterpreter.setStatusTip(
            interpreter or tr('No Python interpreter found'))
        self.actionInterpreter.setToolTip(
            interpreter or tr('No Python interpreter found'))

    # ------------------------------------------------------------ réglages
    def choose_interpreter(self):
        current = find_interpreter() or ''
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.main_window, tr('Select the Python interpreter'),
            current, tr('Python interpreter') + ' (python*.exe python*);;' +
            tr('All files') + ' (*)')
        if not path:
            return None
        try:
            subprocess.run([path, '--version'], capture_output=True,
                           timeout=10, check=True, **no_window_kwargs())
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self.main_window, tr('Invalid interpreter'),
                tr('%s does not seem to be a working Python interpreter.\n\n'
                   '%s') % (path, e))
            return None
        Settings().python_interpreter = path
        return path

    def edit_arguments(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self.main_window, tr('Script arguments'),
            tr('Arguments passed to the script:'),
            QtWidgets.QLineEdit.Normal, Settings().python_arguments)
        if ok:
            Settings().python_arguments = text

    # ----------------------------------------------------------- exécution
    def run(self):
        """
        Enregistre puis exécute le fichier Python courant.
        """
        editor = self.app.edit.current_editor
        if not self.is_python(editor):
            return
        board_files = self.app.micropython.board_files
        if board_files.is_board_file(editor.file.path) or \
                board_files.is_drive_file(editor.file.path):
            # fichier de la carte : il doit s'exécuter sur la carte (ses
            # modules, comme neopixel, n'existent pas sur le PC)
            self.app.micropython.run_on_board()
            return
        interpreter = find_interpreter() or self.choose_interpreter()
        if not interpreter:
            return
        self.ui.tabWidgetEditors.save_all()
        path = editor.file.path
        console = self.ui.consoleOutput
        self.ui.tabWidgetLogs.setCurrentIndex(LOG_PAGE_RUN)
        self.ui.dockWidgetLogs.show()
        console.clear()
        self.app.cobol.enable_run(False)
        console.setFocus(True)
        arguments = ['-u', path] + split_arguments(Settings().python_arguments)
        _logger().info('running %r %r', interpreter, arguments)
        console.start_process(
            interpreter, arguments, working_dir=os.path.dirname(path),
            env=child_environment(), print_command=True)

    def _open_file_at(self, path, line):
        """
        Ouvre le fichier d'un lien de la console (trace d'erreur Python) à la
        ligne indiquée.
        """
        if not os.path.exists(path):
            return
        self.app.file.open_file(path)
        editor = self.app.edit.current_editor
        if editor is not None:
            TextHelper(editor).goto_line(line)
            editor.setFocus(True)
