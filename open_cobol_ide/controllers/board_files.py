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

from pyqode.qt import QtCore, QtWidgets

from open_cobol_ide import system
from open_cobol_ide.settings import Settings
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


def safe_board_id(value):
    """Nom de dossier de cache d'une carte ('auto' si inconnue)."""
    value = ''.join(c if c.isalnum() or c in '._-' else '_'
                    for c in (value or '').strip())
    return value or 'auto'


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
        self._interpreter_key = None

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
    def show_board(self, refresh=True):
        """Affiche la carte (et recharge sa racine)."""
        if not self.button.isChecked():
            self.button.setChecked(True)      # déclenche _on_toggled
        elif refresh:
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
        item = self.tree.itemAt(pos)
        folder = self._folder_item(item)
        menu = QtWidgets.QMenu(self.tree)
        if folder is not None:
            menu.addAction(tr('New file...'),
                           lambda: self._new_entry(folder, False))
            menu.addAction(tr('New folder...'),
                           lambda: self._new_entry(folder, True))
            menu.addSeparator()
        menu.addAction(tr('Refresh'), self.refresh)
        menu.exec_(self.tree.mapToGlobal(pos))

    def _folder_item(self, item):
        """Dossier dans lequel créer : l'élément, son parent, ou la racine."""
        if item is None:
            return self.tree.topLevelItem(0)
        if item.data(0, ROLE_DIR):
            return item
        return item.parent()

    def _new_entry(self, folder, is_dir):
        title = tr('New folder on the board') if is_dir \
            else tr('New file on the board')
        name, ok = QtWidgets.QInputDialog.getText(
            self.controller.main_window, title, tr('Name:'))
        name = name.strip()
        if not ok or not name:
            return
        if '/' in name or chr(92) in name:
            self._error(
                title, tr('The name cannot contain a slash or a backslash'))
            return
        remote = join_remote(folder.data(0, ROLE_PATH), name)
        code = ("import os\nos.mkdir(%r)\n" if is_dir
                else "open(%r, 'w').close()\n") % remote
        self._run(['exec', code], lambda ok, out, err: self._created(
            ok, err or out, folder, remote, is_dir, title))

    def _created(self, ok, message, folder, remote, is_dir, title):
        if not ok:
            self._error(title, message)
            return
        self._reload(folder)
        if not is_dir:
            self.open_remote(remote)

    def _reload(self, item):
        """Relit le contenu d'un dossier de la carte."""
        item.setData(0, ROLE_STATE, None)
        item.takeChildren()
        item.addChild(QtWidgets.QTreeWidgetItem(['...']))
        if item.isExpanded():
            self._on_expanded(item)
        else:
            item.setExpanded(True)      # déclenche le chargement

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
            # (la racine reste affichée, vide : « Actualiser » ou « Nouveau
            # fichier » restent possibles par le clic droit)
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

    @staticmethod
    def cache_dir():
        return os.path.join(system.data_dir(), 'cache', 'board')

    @staticmethod
    def board_id():
        return safe_board_id(Settings().micropython_board)

    def is_board_file(self, path):
        """Vrai si ``path`` est la copie locale d'un fichier de la carte."""
        cache = os.path.normcase(os.path.abspath(self.cache_dir()))
        path = os.path.normcase(os.path.abspath(path))
        return path.startswith(cache + os.sep)

    def split_cache_path(self, path):
        """
        :return: (identifiant de la carte, chemin sur la carte) d'une copie
                 locale, ou None si ce n'en est pas une.
        """
        if not self.is_board_file(path):
            return None
        rel = os.path.relpath(os.path.abspath(path),
                              os.path.abspath(self.cache_dir()))
        parts = rel.split(os.sep)
        if len(parts) < 2:
            return None
        return parts[0], '/' + '/'.join(parts[1:])

    def local_path(self, remote):
        """Copie locale d'un fichier de la carte actuellement choisie."""
        return os.path.join(
            self.cache_dir(), self.board_id(),
            *[p for p in remote.split('/') if p])

    def attach(self, editor):
        """
        Relie un éditeur à sa carte : chaque enregistrement d'une copie de
        fichier de la carte (ouverte depuis le panneau ou depuis les fichiers
        récents) est renvoyé sur la carte d'origine.
        """
        try:
            path = editor.file.path
        except AttributeError:
            return
        if not path or not self.is_board_file(path) or \
                getattr(editor, '_board_linked', False):
            return
        editor._board_linked = True
        editor.text_saved.connect(
            lambda saved, ed=editor: self._on_saved(ed, saved))

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
        self.app.edit.add_editor(local)     # (relié à la carte par attach)
        self.app.view.show_edit_page()

    def _on_saved(self, editor, path):
        info = self.split_cache_path(path)
        if info is None:
            return
        board, remote = info
        if board == self.board_id():
            self._upload(editor, path, remote, None)
            return
        # le fichier vient d'une autre carte que celle choisie : on cherche
        # son port d'après le numéro de série, parmi les cartes branchées
        self._find_port(
            board,
            lambda port: self._upload(editor, path, remote, port),
            lambda: self._not_sent(editor, board))

    def _find_port(self, board, found, missing):
        from .micropython import parse_port_list

        def done(ok, out, err):
            for port, _, serial in parse_port_list(out if ok else ''):
                if safe_board_id(serial or port) == board:
                    found(port)
                    return
            missing()
        self._run(['connect', 'list'], done, raw=True)

    def _not_sent(self, editor, board):
        self._mark_unsaved(editor)
        self._error(
            tr('Cannot save the file on the board'),
            tr('This file belongs to the board "%s", which is not '
               'connected. Nothing was sent: connect it and save again.')
            % board)

    @staticmethod
    def _mark_unsaved(editor):
        """L'onglet redevient « modifié » : la carte n'a pas la dernière version."""
        try:
            editor.document().setModified(True)
        except RuntimeError:
            pass        # onglet fermé entre-temps

    def _upload(self, editor, path, remote, port):
        self._run(['cp', path, ':' + remote],
                  lambda ok, out, err: self._uploaded(
                      ok, err or out, remote, editor), port=port)

    def _uploaded(self, ok, message, remote, editor):
        if ok:
            self._ui.statusbar.showMessage(
                tr('Saved on the board: %s') % remote, 5000)
        else:
            self._mark_unsaved(editor)
            self._error(tr('Cannot save the file on the board'), message)

    # ------------------------------------------------------------- mpremote
    def _error(self, title, message):
        message = str(message).strip()[-1500:]
        if 'could not enter raw repl' in message:
            from .micropython import RAW_REPL_HINT
            message += '\n\n' + RAW_REPL_HINT()
        QtWidgets.QMessageBox.warning(
            self.controller.main_window, title, message)

    def shutdown(self):
        """À la fermeture de l'IDE : plus de commande en attente."""
        self._queue.clear()
        job = self._job
        if job is not None:
            if not job.wait(3000):
                job.terminate()
                job.wait(500)

    def _run(self, args, callback, port=None, raw=False):
        """
        Met la commande mpremote en file d'attente (une à la fois).

        :param port: port à utiliser à la place de celui des préférences
        :param raw: args est la ligne de commande complète de mpremote
        """
        self._queue.append((args, callback, port, raw))
        self._next()

    def _next(self):
        if self._job is not None or not self._queue:
            return
        key = Settings().python_interpreter
        if self._interpreter is None or key != self._interpreter_key:
            self._interpreter = self.controller._prepare()
            self._interpreter_key = key
            if not self._interpreter:
                self._queue.clear()
                return
        args, callback, port, raw = self._queue.pop(0)
        if raw:
            command = [self._interpreter, '-u', '-m', 'mpremote'] + args
        else:
            command = [self._interpreter] + \
                self.controller._args(*args, port=port)
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
