"""
Contrôleur MicroPython : sous-menu du menu Python pour travailler avec une
carte reliée en USB (port COM virtuel), au moyen de ``mpremote``.

``mpremote`` n'est pas embarqué dans l'IDE : il est lancé avec l'interpréteur
Python choisi dans le menu Python (``python -m mpremote``). Si le module
manque, l'IDE propose de l'installer avec pip.
"""
import logging
import os
import subprocess
import sys

from pyqode.qt import QtWidgets

from open_cobol_ide.settings import Settings
from open_cobol_ide.translation import tr
from .base import Controller
from .cobol import LOG_PAGE_RUN
from .board_files import BoardFilesPanel
from .python import child_environment, find_interpreter, no_window_kwargs


def _logger():
    return logging.getLogger(__name__)


def parse_port_list(text):
    """
    Analyse la sortie de ``mpremote connect list`` : une ligne par carte, le
    premier mot étant le nom du port (COM5, /dev/ttyACM0...).

    :return: liste de (port, ligne de description)
    """
    ports = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if parts:
            ports.append((parts[0], line.strip()))
    return ports


class MicroPythonController(Controller):
    """
    Contrôle le sous-menu MicroPython.
    """
    def __init__(self, app):
        super().__init__(app)
        menu = self.app.python.menu
        menu.addSeparator()
        self.submenu = menu.addMenu(tr('&MicroPython'))
        self.actionPort = self._add(tr('Board &port...'), self.choose_port)
        self.actionRun = self._add(
            tr('&Run on the board'), self.run_on_board, 'Shift+F5')
        self.actionSend = self._add(
            tr('&Send to the board...'), self.send_to_board)
        self.actionFiles = self._add(
            tr('&Files on the board'), self.list_files)
        self.actionResetBefore = self._add(
            tr('Reset the board &before running'), self.toggle_reset)
        self.actionResetBefore.setCheckable(True)
        self.actionResetBefore.setChecked(Settings().micropython_reset)
        self.actionReset = self._add(
            tr('Soft re&set the board'), self.soft_reset)
        self.actionRepl = self._add(
            tr('Open the R&EPL in a window'), self.open_repl)
        self.submenu.addSeparator()
        self.board_files = BoardFilesPanel(self)
        self.actionInstall = self._add(
            tr('&Install or update mpremote'), self.install_mpremote)

    def _add(self, text, slot, shortcut=None):
        action = QtWidgets.QAction(text, self.main_window)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False: slot())
        self.submenu.addAction(action)
        return action

    # ------------------------------------------------------------ outils
    def _info(self, title, text):
        QtWidgets.QMessageBox.information(self.main_window, title, text)

    @staticmethod
    def _has_mpremote(interpreter):
        try:
            return subprocess.run(
                [interpreter, '-c', 'import mpremote'], capture_output=True,
                timeout=30, **no_window_kwargs()).returncode == 0
        except Exception:
            return False

    def _prepare(self):
        """
        Vérifie que la console est libre, qu'un interpréteur Python est
        disponible et que mpremote est installé.

        :return: le chemin de l'interpréteur, ou None
        """
        if self.ui.consoleOutput.is_running:
            self._info(tr('Console busy'),
                       tr('A program is already running in the console.'))
            return None
        interpreter = find_interpreter() or self.app.python.choose_interpreter()
        if not interpreter:
            return None
        if not self._has_mpremote(interpreter):
            answer = QtWidgets.QMessageBox.question(
                self.main_window, tr('mpremote not installed'),
                tr('The mpremote tool is not installed for this Python '
                   'interpreter.\n\nInstall it now with pip?'))
            if answer == QtWidgets.QMessageBox.Yes:
                self._start(interpreter,
                            ['-m', 'pip', 'install', '--upgrade', 'mpremote'])
            return None
        return interpreter

    @staticmethod
    def _args(*args, reset=False):
        """
        Arguments de ``python`` pour lancer mpremote vers la bonne carte.

        Sans ``reset``, la commande ``resume`` est ajoutée : mpremote ne
        redémarre pas la carte en entrant dans le REPL brut. Il attend sinon
        le texte anglais « soft reboot », que les cartes dont le système est
        traduit (CircuitPython en français...) n'envoient pas.
        """
        cmd = ['-u', '-m', 'mpremote']
        port = Settings().micropython_port
        if port:
            cmd += ['connect', port]
        if not reset:
            cmd.append('resume')
        return cmd + list(args)

    def _start(self, interpreter, arguments, working_dir=None):
        ui = self.ui
        ui.tabWidgetLogs.setCurrentIndex(LOG_PAGE_RUN)
        ui.dockWidgetLogs.show()
        self.app.cobol.enable_run(False)
        _logger().info('running %r %r', interpreter, arguments)
        ui.consoleOutput.start_process(
            interpreter, arguments, working_dir=working_dir,
            env=child_environment(), print_command=True)

    def _current_python_file(self):
        editor = self.app.edit.current_editor
        if not self.app.python.is_python(editor):
            return None
        self.ui.tabWidgetEditors.save_all()
        return editor.file.path

    # ------------------------------------------------------------ actions
    def choose_port(self):
        interpreter = self._prepare()
        if not interpreter:
            return
        try:
            result = subprocess.run(
                [interpreter, '-m', 'mpremote', 'connect', 'list'],
                capture_output=True, timeout=30, encoding='utf-8',
                errors='replace', env=dict(os.environ, **child_environment()),
                **no_window_kwargs())
        except Exception as e:
            self._info(tr('Board port'), str(e))
            return
        ports = parse_port_list(result.stdout)
        if not ports:
            self._info(
                tr('Board port'),
                tr('No board detected.\n\nCheck the USB cable and that no '
                   'other program (Thonny, a terminal...) uses the port.'))
            return
        labels = [tr('Automatic detection')] + [line for _, line in ports]
        current = Settings().micropython_port
        index = 0
        for i, (device, _) in enumerate(ports, 1):
            if device == current:
                index = i
        label, ok = QtWidgets.QInputDialog.getItem(
            self.main_window, tr('Board port'),
            tr('Serial port of the board:'), labels, index, False)
        if ok:
            position = labels.index(label)
            Settings().micropython_port = \
                '' if position == 0 else ports[position - 1][0]
            self.board_files.reset()

    def run_on_board(self):
        """Exécute le fichier courant sur la carte, sans l'y enregistrer."""
        path = self._current_python_file()
        if path is None:
            return
        interpreter = self._prepare()
        if interpreter:
            reset = Settings().micropython_reset
            self._start(interpreter, self._args('run', path, reset=reset),
                        os.path.dirname(path))

    def send_to_board(self):
        """Copie le fichier courant sur la carte (par exemple en main.py)."""
        path = self._current_python_file()
        if path is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self.main_window, tr('Send to the board'),
            tr('File name on the board:'), QtWidgets.QLineEdit.Normal,
            os.path.basename(path))
        name = name.strip().lstrip(':/') if ok else ''
        if not name:
            return
        interpreter = self._prepare()
        if interpreter:
            self._start(interpreter, self._args('cp', path, ':' + name),
                        os.path.dirname(path))

    def list_files(self):
        self.board_files.show_board()

    def toggle_reset(self):
        Settings().micropython_reset = self.actionResetBefore.isChecked()

    def soft_reset(self):
        interpreter = self._prepare()
        if interpreter:
            self._start(interpreter, self._args('soft-reset', reset=True))

    def open_repl(self):
        """
        Ouvre le REPL de la carte dans une fenêtre console séparée (mpremote
        a besoin d'un vrai terminal : la console de l'IDE ne convient pas).
        """
        if sys.platform != 'win32':
            self._info(tr('REPL'),
                       tr('The REPL window is only available on Windows. Run '
                          '"python -m mpremote repl" in a terminal.'))
            return
        interpreter = self._prepare()
        if not interpreter:
            return
        subprocess.Popen(
            [interpreter] + self._args('repl', reset=True),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=dict(os.environ, **child_environment()))

    def install_mpremote(self):
        if self.ui.consoleOutput.is_running:
            self._info(tr('Console busy'),
                       tr('A program is already running in the console.'))
            return
        interpreter = find_interpreter() or self.app.python.choose_interpreter()
        if interpreter:
            self._start(interpreter,
                        ['-m', 'pip', 'install', '--upgrade', 'mpremote'])
