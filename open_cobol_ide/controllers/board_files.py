"""
Fichiers de la carte MicroPython / CircuitPython dans le panneau « Système de
fichiers » : un bouton bascule entre les fichiers du PC et ceux de la carte.

Tout passe par ``mpremote`` (avec ``resume`` : voir MicroPythonController),
dans un fil d'exécution séparé pour ne pas figer l'interface. Un fichier de la
carte est copié dans ``data/cache/board/`` puis ouvert dans l'éditeur ; à
chaque enregistrement, il est renvoyé sur la carte.
"""
import logging
import os
import subprocess

from pyqode.qt import QtCore, QtGui, QtWidgets

from open_cobol_ide import system
from open_cobol_ide.translation import tr
from open_cobol_ide.view.widgets import FileIconProvider
from .python import child_environment, no_window_kwargs


def _logger():
    return logging.getLogger(__name__)


# Liste d'un dossier : une ligne « D|F <tab> taille <tab> nom » par entrée.
# Seulement os.listdir et os.stat (os.ilistdir manque à CircuitPython).
LIST_DIR_CODE = (
    "import os\n"
    "p = %r\n"
    "for n in sorted(os.listdir(p)):\n"
    "    s = os.stat(p.rstrip('/') + '/' + n)\n"
    "    print('D' if s[0] & 0x4000 else 'F', s[6], n, sep='\\t')\n"
)

ROLE_PATH = QtCore.Qt.UserRole
ROLE_DIR = QtCore.Qt.UserRole + 1
ROLE_STATE = QtCore.Qt.UserRole + 2     # None, 'loading' ou 'loaded'


def parse_listing(text):
    """
    :return: liste de (est_dossier, taille, nom), dossiers d'abord.
    """
    entries = []
    for line in text.splitlines():
        parts = line.rstrip('\r').split('\t', 2)
        if len(parts) == 3 and parts[0] in ('D', 'F'):
            try:
                size = int(parts[1])
            except ValueError:
                size = 0
            entries.append((parts[0] == 'D', size, parts[2]))
    entries.sort(key=lambda e: (not e[0], e[2].lower()))
    return entries


def join_remote(folder, name):
    return folder.rstrip('/') + '/' + name


class _Job(QtCore.QThread):
    """Exécute une commande dans un fil séparé."""
    done = QtCore.Signal(int, str, str)

    def __init__(self, command, env, parent=None):
        super().__init__(parent)
        self._command = command
        self._env = env

    def run(self):
        try:
            result = subprocess.run(
                self._command, capture_output=True, timeout=30,
                encoding='utf-8', errors='replace', env=self._env,
                **no_window_kwargs())
        except Exception as e:
            self.done.emit(-1, '', str(e))
        else:
            self.done.emit(result.returncode, result.stdout, result.stderr)


class BoardFilesPanel(QtCore.QObject):
    """
    Arbre des fichiers de la carte, placé dans le dock « Système de fichiers ».
    """
    def __init__(self, controller):
        super().__init__(controller.main_window)
        self.controller = controller
        self.app = controller.app
        ui = controller.ui
        self._ui = ui
        self._queue = []
        self._job = None
        self._interpreter = None
        self._remote_of = {}     # chemin local normalisé -> chemin sur la carte

        self.tree = QtWidgets.QTreeWidget(ui.dockWidgetContents_4)
        self.tree.setHeaderHidden(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.hide()
        ui.verticalLayout.addWidget(self.tree)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        self.button = QtWidgets.QToolButton(ui.dockWidgetContents_4)
        self.button.setText(tr('Board'))
        self.button.setCheckable(True)
        self.button.setToolTip(
            tr('Show the files of the board instead of those of the PC'))
        self.button.toggled.connect(self._on_toggled)
        ui.horizontalLayout_2.insertWidget(0, self.button)

        self._folder_icon = FileIconProvider().icon(
            QtCore.QFileInfo(system.app_dir()))

    # ------------------------------------------------------------ affichage
    def show_board(self):
        """Affiche la carte (et recharge sa racine)."""
        if not self.button.isChecked():
            self.button.setChecked(True)      # déclenche _on_toggled
        else:
            self.refresh()
        self._ui.dockWidgetFileSystem.show()

    def _on_toggled(self, checked):
        ui = self._ui
        ui.tvFileSystem.setVisible(not checked)
        ui.bt_fs_up.setVisible(not checked)
        ui.btFSLock.setVisible(not checked)
        self.tree.setVisible(checked)
        if checked and self.tree.topLevelItemCount() == 0:
            self.refresh()

    def reset(self):
        """La carte (ou le port) a changé : on oublie l'arborescence."""
        self.tree.clear()
        if self.button.isChecked():
            self.refresh()

    def refresh(self):
        self.tree.clear()
        root = self._new_item('/', True)
        root.setText(0, '/')
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)       # déclenche le chargement

    def _new_item(self, name, is_dir, path=None, size=0):
        item = QtWidgets.QTreeWidgetItem([name])
        item.setData(0, ROLE_PATH, path or name)
        item.setData(0, ROLE_DIR, is_dir)
        if is_dir:
            item.setIcon(0, self._folder_icon)
            item.addChild(QtWidgets.QTreeWidgetItem(['...']))   # flèche
        else:
            item.setIcon(0, FileIconProvider().icon(QtCore.QFileInfo(name)))
            item.setToolTip(0, tr('%d bytes') % size)
        return item

    def _context_menu(self, pos):
        menu = QtWidgets.QMenu(self.tree)
        menu.addAction(tr('Refresh'), self.refresh)
        menu.exec_(self.tree.mapToGlobal(pos))

    # ------------------------------------------------------------- actions
    def _on_expanded(self, item):
        if item.data(0, ROLE_DIR) and item.data(0, ROLE_STATE) is None:
            item.setData(0, ROLE_STATE, 'loading')
            folder = item.data(0, ROLE_PATH)
            self._run(['exec', LIST_DIR_CODE % folder],
                      lambda ok, out, err: self._listing(item, ok, out, err))

    def _listing(self, item, ok, out, err):
        try:
            item.takeChildren()
        except RuntimeError:
            return      # arbre vidé entre-temps (actualisation)
        if not ok:
            item.setData(0, ROLE_STATE, None)
            item.setExpanded(False)
            if item.parent() is None:
                # carte illisible (débranchée, mauvais port...) : on ne garde
                # pas l'arborescence précédente
                self.tree.clear()
            self._error(tr('Cannot read the board'), err or out)
            return
        item.setData(0, ROLE_STATE, 'loaded')
        folder = item.data(0, ROLE_PATH)
        for is_dir, size, name in parse_listing(out):
            item.addChild(self._new_item(
                name, is_dir, join_remote(folder, name), size))

    def _on_activated(self, item, column=0):
        if item.data(0, ROLE_DIR):
            item.setExpanded(not item.isExpanded())
        else:
            self.open_remote(item.data(0, ROLE_PATH))

    def local_path(self, remote):
        return os.path.join(
            system.data_dir(), 'cache', 'board',
            *[p for p in remote.split('/') if p])

    def open_remote(self, remote):
        local = self.local_path(remote)
        key = os.path.normcase(os.path.abspath(local))
        for editor in self._ui.tabWidgetEditors.widgets(include_clones=True):
            if os.path.normcase(os.path.abspath(editor.file.path)) == key:
                self.app.edit.add_editor(local)     # simple mise au premier plan
                self.app.view.show_edit_page()
                return
        os.makedirs(os.path.dirname(local), exist_ok=True)
        self._run(['cp', ':' + remote, local],
                  lambda ok, out, err: self._downloaded(
                      ok, err or out, remote, local))

    def _downloaded(self, ok, message, remote, local):
        if not ok or not os.path.isfile(local):
            self._error(tr('Cannot read the file from the board'), message)
            return
        self._remote_of[os.path.normcase(os.path.abspath(local))] = remote
        editor = self.app.edit.add_editor(local)
        self.app.view.show_edit_page()
        if editor is not None:
            editor.text_saved.connect(self._on_saved)

    def _on_saved(self, path):
        remote = self._remote_of.get(os.path.normcase(os.path.abspath(path)))
        if remote is None:
            return
        self._run(['cp', path, ':' + remote],
                  lambda ok, out, err: self._uploaded(ok, err or out, remote))

    def _uploaded(self, ok, message, remote):
        if ok:
            self._ui.statusbar.showMessage(
                tr('Saved on the board: %s') % remote, 5000)
        else:
            self._error(tr('Cannot save the file on the board'), message)

    # ------------------------------------------------------------- mpremote
    def _error(self, title, message):
        QtWidgets.QMessageBox.warning(
            self.controller.main_window, title, str(message).strip()[-1500:])

    def _run(self, args, callback):
        """Met la commande mpremote en file d'attente (une à la fois)."""
        self._queue.append((args, callback))
        self._next()

    def _next(self):
        if self._job is not None or not self._queue:
            return
        if self._interpreter is None:
            self._interpreter = self.controller._prepare()
            if not self._interpreter:
                self._queue.clear()
                return
        args, callback = self._queue.pop(0)
        command = [self._interpreter] + self.controller._args(*args)
        env = dict(os.environ, **child_environment())
        job = _Job(command, env, self)
        job.done.connect(
            lambda code, out, err: self._finished(callback, code, out, err))
        self._job = job
        self._ui.statusbar.showMessage(tr('Talking to the board...'))
        job.start()

    def _finished(self, callback, code, out, err):
        job, self._job = self._job, None
        if job is not None:
            job.wait()
        self._ui.statusbar.clearMessage()
        _logger().debug('mpremote -> %s %r %r', code, out[:200], err[:200])
        try:
            callback(code == 0, out, err)
        finally:
            self._next()
