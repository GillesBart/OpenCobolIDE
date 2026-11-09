"""
Fichiers de la carte MicroPython / CircuitPython dans le panneau « Système de
fichiers » : un bouton bascule entre les fichiers du PC et ceux de la carte.

Tout passe par ``mpremote`` (avec ``resume`` : voir MicroPythonController),
dans un fil d'exécution séparé pour ne pas figer l'interface. Un fichier de la
carte est copié dans ``data/cache/board/`` puis ouvert dans l'éditeur ; à
chaque enregistrement, il est renvoyé sur la carte.
"""
import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import time

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
# Dit si ``p`` existe déjà sur la carte (EXISTS/NEW), sans rien écrire.
CHECK_PATH_CODE = (
    "import os\n"
    "p = %r\n"
    "try:\n"
    "    os.stat(p)\n"
    "    print('EXISTS')\n"
    "except OSError:\n"
    "    print('NEW')\n"
)

# Création des dossiers manquants de ``p`` (fragment commun).
_MKDIRS = (
    "cur = ''\n"
    "for part in p.split(chr(47))[:-1]:\n"
    "    if part:\n"
    "        cur = cur + chr(47) + part\n"
    "        try:\n"
    "            os.mkdir(cur)\n"
    "        except OSError:\n"
    "            pass\n"
)

# Crée un nouveau fichier ``p`` avec son contenu en UNE écriture : sur
# CircuitPython, une seconde écriture juste après la première tombe pendant le
# rechargement de la carte (système de fichiers en lecture seule).
WRITE_NEW_CODE = (
    "import os\n"
    "p = %r\n"
    "d = %r\n"
    + _MKDIRS +
    "f = open(p, 'wb')\n"
    "f.write(d)\n"
    "f.close()\n"
)
# taille maximale du contenu (repr) envoyé par cette voie : la ligne de
# commande Windows est limitée à environ 32 000 caractères
WRITE_NEW_MAX = 16000

# Crée les dossiers manquants et le fichier vide ``p``. « mpremote cp » interroge
# d'abord la carte sur la destination et n'accepte que l'erreur ENOENT en
# anglais : sur une carte dont le système est traduit (CircuitPython en
# français), copier vers un fichier inexistant échoue. Un fichier déjà créé
# évite ce cas.
TOUCH_PATH_CODE = (
    "import os\n"
    "p = %r\n"
    + _MKDIRS +
    "open(p, 'w').close()\n"
)

# Délais (ms) : CircuitPython recharge son programme dès qu'un fichier est
# écrit, et pendant ce rechargement le système de fichiers est en lecture
# seule pour le code. On attend avant d'écrire à nouveau.
AFTER_CREATE_DELAY = 2500
READ_ONLY_RETRY_DELAY = 2000
READ_ONLY_RETRIES = 3

# Une carte absente de la liste pendant ce nombre de vérifications de suite
# est considérée comme débranchée (un redémarrage matériel la fait disparaître
# une ou deux secondes).
ABSENT_POLLS = 2

# Âge maximal (secondes) de la dernière liste des cartes branchées.
PORTS_MAX_AGE = 4.0


def _candidate_roots():
    """Racines des volumes amovibles du PC (lecteurs USB)."""
    roots = []
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            mask = kernel.GetLogicalDrives()
            for i in range(26):
                root = chr(65 + i) + ':\\'
                if mask & (1 << i) and kernel.GetDriveTypeW(root) == 2:
                    roots.append(root)        # 2 : DRIVE_REMOVABLE
        except Exception:
            _logger().exception('cannot list the drives')
    else:
        for pattern in ('/media/*/*', '/run/media/*/*', '/Volumes/*',
                        '/mnt/*'):
            roots += glob.glob(pattern)
    return roots


def circuitpython_drives(roots=None):
    """
    Les lecteurs USB de cartes CircuitPython montés par le PC : liste de
    (racine, UID). Le fichier boot_out.txt du lecteur contient « UID:<numéro
    de série> », le même numéro que celui du port série de la carte.
    """
    found = []
    for root in (_candidate_roots() if roots is None else roots):
        try:
            with open(os.path.join(root, 'boot_out.txt'), encoding='utf-8',
                      errors='replace') as f:
                text = f.read(2000)
        except OSError:
            continue
        if 'CircuitPython' not in text:
            continue
        match = re.search(r'UID:([0-9A-Fa-f]+)', text)
        found.append((root, match.group(1).upper() if match else None))
    return found


def find_circuitpython_drive(board, roots=None):
    """
    Le lecteur USB (CIRCUITPY) de la carte ``board``, ou None.

    Quand le PC l'a monté, les fichiers de la carte sont de vrais fichiers
    du PC : l'IDE les ouvre et les enregistre directement, sans passer par le
    port série (et un programme de la carte n'a de toute façon plus le droit
    d'y écrire).
    """
    if not board:
        return None
    for root, uid in circuitpython_drives(roots):
        if uid is not None and uid == board.upper():
            return root
    return None


def is_read_only_error(message):
    return any(key in message for key in
               ('Errno 30', 'ead-only', 'lecture seule'))


DELETE_FILE_CODE = "import os\np = %r\nos.remove(p)\n"
DELETE_DIR_CODE = "import os\np = %r\nos.rmdir(p)\n"

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
        #: identité de la carte dont l'arborescence est affichée (None : aucune)
        self._shown_id = None
        self._pending = 0           # envois de fichiers en cours
        self._batch_ok = True
        self._after = []            # actions à faire quand les envois ont fini
        # surveille le changement de carte tant que le panneau est affiché
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        # ancien emplacement du cache (data/cache/board) : plus utilisé
        shutil.rmtree(os.path.join(system.data_dir(), 'cache', 'board'),
                      ignore_errors=True)
        self._purge_loose_cache()
        QtCore.QTimer.singleShot(300, self._poll)     # première vérification
        self._ports_seen = None     # (heure, liste des cartes branchées)
        #: cartes branchées (None : pas encore vérifié) et compteur d'absences
        self._connected = None
        self._absent = {}

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
        if checked:
            self.refresh()          # toujours relire : la carte a pu changer
        elif ui.tvFileSystem.root_path is None:
            ui.tvFileSystem.set_root_path(Settings().last_path)

    def reset(self):
        """La carte (ou le port) a changé : on oublie l'arborescence."""
        self.tree.clear()
        if self.button.isChecked():
            self.refresh()

    def refresh(self):
        """Efface l'arborescence et relit la carte actuellement branchée."""
        self.tree.clear()
        self._shown_id = None
        self._with_board(self._load_root, on_missing=self._show_no_board)

    def _load_root(self, board, port=None):
        self._shown_id = board
        self.tree.clear()
        root = self._new_item('/', True)
        root.setText(0, '/')
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)       # déclenche le chargement

    def _show_no_board(self):
        self._shown_id = None
        self.tree.clear()
        item = QtWidgets.QTreeWidgetItem([tr('No board detected.')])
        item.setFlags(QtCore.Qt.NoItemFlags)
        item.setData(0, ROLE_PATH, None)
        self.tree.addTopLevelItem(item)

    # ---------------------------------------------- changement de carte
    def _poll(self):
        """
        Vérifie régulièrement les cartes branchées : l'arborescence affichée
        suit la carte courante, et le cache des cartes débranchées est effacé.
        """
        if self._job is not None or self._queue:
            return
        if self._interpreter is None and \
                self.controller.quiet_interpreter() is None:
            # mpremote pas prêt : aucune carte n'est utilisable, donc aucun
            # fichier de carte ne doit rester ouvrable
            if self._connected is None:
                self._update_connected(set())
            return
        self._run(['connect', 'list'], self._polled, raw=True)

    def _purge_loose_cache(self):
        """
        Efface ce qui traîne à la racine du cache (anciennes versions : fichiers
        sans dossier de carte), qui ne se rattache à aucune carte.
        """
        try:
            for name in os.listdir(self.cache_dir()):
                path = os.path.join(self.cache_dir(), name)
                if os.path.isfile(path):
                    os.remove(path)
        except OSError:
            pass

    def recent_available(self, path):
        """
        Vrai si ``path`` peut figurer dans les fichiers récents : un fichier
        ordinaire qui existe, ou un fichier de carte dont la carte est là.
        """
        info = self.split_cache_path(path)
        if info is not None:
            return self._connected is not None and info[0] in self._connected
        if self.is_board_file(path) or Settings._in_cache(path):
            return False        # reste du cache sans carte
        return os.path.exists(path)

    def _cache_boards(self):
        try:
            return [d for d in os.listdir(self.cache_dir())
                    if os.path.isdir(os.path.join(self.cache_dir(), d))]
        except OSError:
            return []

    def _update_connected(self, listed):
        """
        Met à jour la liste des cartes branchées et efface le cache des cartes
        qui ne le sont plus. Les onglets déjà ouverts restent ouverts : leur
        fichier n'existe plus, on ne peut qu'« Enregistrer sous ».
        """
        previous = self._connected or set()
        present = set(listed)
        for board in set(previous) | set(self._cache_boards()):
            if board in listed:
                self._absent.pop(board, None)
                continue
            count = self._absent.get(board, 0) + 1
            self._absent[board] = count
            if count < ABSENT_POLLS and board in previous:
                present.add(board)      # peut-être un simple redémarrage
            elif self._pending == 0:
                self._purge_board(board)
        if present != self._connected:
            self._connected = present
            self._refresh_recents()

    def _purge_board(self, board):
        """Efface le cache d'une carte débranchée."""
        folder = os.path.join(self.cache_dir(), board)
        prefix = os.path.normcase(os.path.abspath(folder)) + os.sep
        for editor in self._ui.tabWidgetEditors.widgets(include_clones=True):
            try:
                path = os.path.normcase(os.path.abspath(editor.file.path))
            except (AttributeError, TypeError):
                continue
            if path.startswith(prefix):
                self._freeze_editor(editor)
        shutil.rmtree(folder, ignore_errors=True)
        self._absent.pop(board, None)
        self._refresh_recents()

    @staticmethod
    def _freeze_editor(editor):
        """
        L'éditeur reste ouvert alors que son fichier va disparaître : on
        arrête la surveillance du fichier (qui proposerait de fermer l'onglet).
        """
        try:
            from pyqode.core.modes import FileWatcherMode
            watcher = editor.modes.get(FileWatcherMode)
            watcher._timer.stop()
            watcher._mtime = 0
        except (KeyError, AttributeError):
            pass

    def _refresh_recents(self):
        """Les fichiers récents d'une carte ne sont proposés que si elle est là."""
        files = getattr(self.app, 'file', None)
        if files is not None:
            files.recent_files_manager.updated.emit()

    def _adopt_only_board(self, ports):
        """
        Le port choisi n'existe plus (autre carte branchée sous un autre
        numéro) : s'il n'y a qu'une carte, on la suit.
        """
        if len(ports) != 1:
            return None
        port, _, serial = ports[0]
        Settings().micropython_port = port
        Settings().micropython_board = serial or port
        return ports[0]

    def _polled(self, ok, out, err):
        """Compare la carte branchée à celle dont on affiche les fichiers."""
        if not ok:
            return
        ports = self._note_ports(out)
        self._update_connected(
            {safe_board_id(serial or port) for port, _, serial in ports})
        if not self.button.isChecked():
            return
        found = self._resolve_board(ports)
        current = found[0] if found else None
        if current == self._shown_id:
            return
        if current is None:
            self._show_no_board()
        else:
            self._load_root(current)

    def _note_ports(self, out):
        """
        Retient les cartes branchées (liste de mpremote) et met les réglages en
        accord avec elles : une autre carte peut avoir pris le même numéro de
        port, ou la carte choisie avoir été remplacée par une seule autre.
        """
        from .micropython import parse_port_list
        ports = parse_port_list(out)
        self._ports_seen = (time.monotonic(), ports)
        chosen = Settings().micropython_port
        if chosen:
            for port, _, serial in ports:
                if port == chosen:
                    if safe_board_id(serial or port) != self.board_id():
                        Settings().micropython_board = serial or port
                    break
            else:
                self._adopt_only_board(ports)
        return ports

    def _resolve_board(self, ports):
        """
        :return: (identifiant, port) de la carte utilisée parmi ``ports``, ou
                 None. Port choisi dans les préférences, sinon la première
                 carte USB (comme le fait mpremote).
        """
        chosen = Settings().micropython_port
        for port, _, serial in ports:
            if not chosen or port == chosen:
                return safe_board_id(serial or port), port
        return None

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
        if item is not None and item.data(0, ROLE_PATH) not in (None, '/'):
            menu.addAction(tr('Delete...'), lambda: self._delete(item))
            menu.addSeparator()
        if folder is not None:
            menu.addAction(tr('New file...'),
                           lambda: self._new_entry(folder, False))
            menu.addAction(tr('New folder...'),
                           lambda: self._new_entry(folder, True))
            menu.addSeparator()
        menu.addAction(tr('Refresh'), self.refresh)
        menu.exec_(self.tree.mapToGlobal(pos))

    def _delete(self, item):
        """Supprime un fichier (ou un dossier vide) de la carte, après accord."""
        remote = item.data(0, ROLE_PATH)
        is_dir = bool(item.data(0, ROLE_DIR))
        text = tr('Delete the folder "%s" from the board?') if is_dir \
            else tr('Delete the file "%s" from the board?')
        if QtWidgets.QMessageBox.question(
                self.controller.main_window, tr('Delete'),
                text % remote + '\n' + tr('This cannot be undone.'),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        board = self._shown_id or self.board_id()
        drive = self.drive_of(board)
        if drive:
            target = self.drive_path(drive, remote)
            try:
                os.rmdir(target) if is_dir else os.remove(target)
            except OSError as e:
                self._error(tr('Delete'), str(e))
                return
            self._deleted(True, '', remote, is_dir, board, item.parent())
            return
        code = (DELETE_DIR_CODE if is_dir else DELETE_FILE_CODE) % remote
        self._run(['exec', code],
                  lambda ok, out, err: self._deleted(
                      ok, err or out, remote, is_dir, board, item.parent()))

    def _deleted(self, ok, message, remote, is_dir, board, parent):
        if not ok and is_read_only_error(message):
            ok = self._delete_via_drive(remote, is_dir, board)
        if not ok:
            self._error(tr('Delete'), message)
            return
        self._forget_cache_copy(remote, board)
        if parent is not None:
            try:
                self._reload(parent)
            except RuntimeError:
                self.refresh()

    def _delete_via_drive(self, remote, is_dir, board):
        target = self._drive_target(board, remote)
        if target is None:
            return False
        try:
            if is_dir:
                os.rmdir(target)
            else:
                os.remove(target)
        except OSError:
            _logger().exception('cannot delete on the drive')
            return False
        return True

    def _forget_cache_copy(self, remote, board):
        """
        Efface la copie locale d'un fichier supprimé de la carte. Un onglet
        ouvert dessus reste ouvert : il faudra « Enregistrer sous ».
        """
        local = self.local_path(remote, board)
        key = os.path.normcase(os.path.abspath(local))
        for editor in self._ui.tabWidgetEditors.widgets(include_clones=True):
            try:
                if os.path.normcase(os.path.abspath(editor.file.path)) == key:
                    self._freeze_editor(editor)
            except (AttributeError, TypeError):
                continue
        try:
            if os.path.isdir(local):
                shutil.rmtree(local, ignore_errors=True)
            elif os.path.exists(local):
                os.remove(local)
        except OSError:
            pass
        self._refresh_recents()

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
        drive = self.drive_of(self._shown_id or self.board_id())
        if drive:
            target = self.drive_path(drive, remote)
            try:
                if is_dir:
                    os.makedirs(target)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    open(target, 'xb').close()      # 'x' : pas d'écrasement
            except OSError as e:
                self._error(title, str(e))
                return
            self._created(True, '', folder, remote, is_dir, title)
            return
        code = ("import os\nos.mkdir(%r)\n" if is_dir
                else "open(%r, 'w').close()\n") % remote
        self._run(['exec', code], lambda ok, out, err: self._created(
            ok, err or out, folder, remote, is_dir, title))

    def _created(self, ok, message, folder, remote, is_dir, title):
        if not ok and is_read_only_error(message):
            board = self._shown_id or self.board_id()
            ok = self._create_via_drive(remote, is_dir, board)
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
    # ------------------------------------------- carte avec lecteur USB
    def drive_of(self, board):
        """Racine du lecteur USB de la carte (CircuitPython monté), ou None."""
        return find_circuitpython_drive(board)

    @staticmethod
    def drive_path(drive, remote):
        return os.path.join(drive, *[p for p in remote.split('/') if p])

    def is_drive_file(self, path):
        """Vrai si ``path`` est un fichier d'un lecteur de carte CircuitPython."""
        path = os.path.normcase(os.path.abspath(path))
        for root, _ in circuitpython_drives():
            root = os.path.normcase(os.path.abspath(root)).rstrip(os.sep)
            if path.startswith(root + os.sep):
                return True
        return False

    def _list_drive(self, item, drive):
        """Contenu d'un dossier lu directement sur le lecteur."""
        target = self.drive_path(drive, item.data(0, ROLE_PATH))
        entries = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    is_dir = entry.is_dir()
                    size = 0 if is_dir else entry.stat().st_size
                    entries.append((is_dir, size, entry.name))
        except OSError as e:
            self._listing(item, False, '', str(e))
            return
        entries.sort(key=lambda e: (not e[0], e[2].lower()))
        self._fill(item, entries)

    def _on_expanded(self, item):
        if item.data(0, ROLE_DIR) and item.data(0, ROLE_STATE) is None:
            item.setData(0, ROLE_STATE, 'loading')
            folder = item.data(0, ROLE_PATH)
            drive = self.drive_of(self._shown_id)
            if drive:
                self._list_drive(item, drive)
                return
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
        self._fill(item, parse_listing(out))

    def _fill(self, item, entries):
        try:
            item.takeChildren()
        except RuntimeError:
            return
        item.setData(0, ROLE_STATE, 'loaded')
        folder = item.data(0, ROLE_PATH)
        for is_dir, size, name in entries:
            item.addChild(self._new_item(
                name, is_dir, join_remote(folder, name), size))

    def _on_activated(self, item, column=0):
        if item.data(0, ROLE_PATH) is None:
            return          # ligne « aucune carte »
        if item.data(0, ROLE_DIR):
            item.setExpanded(not item.isExpanded())
        else:
            self.open_remote(item.data(0, ROLE_PATH))

    @staticmethod
    def cache_dir():
        return system.board_cache_dir()

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

    def local_path(self, remote, board):
        """Copie locale d'un fichier de la carte ``board``."""
        return os.path.join(
            self.cache_dir(), board, *[p for p in remote.split('/') if p])

    def _with_board(self, callback, on_missing=None):
        """
        Appelle ``callback(identifiant, port)`` pour la carte utilisée.

        L'identité de la carte est relevée en direct (liste des cartes
        branchées, au plus vieille de quelques secondes) : le numéro de port
        mémorisé peut avoir été repris par une autre carte, et les fichiers
        doivent être rangés sous la carte réelle.
        """
        def missing():
            if on_missing is not None:
                on_missing()
            else:
                self._error(tr('Board port'), tr('No board detected.'))

        def resolve(ports):
            found = self._resolve_board(ports)
            if found is None:
                missing()
            else:
                callback(*found)

        seen = self._ports_seen
        if seen is not None and time.monotonic() - seen[0] < PORTS_MAX_AGE:
            resolve(seen[1])
            return

        def done(ok, out, err):
            resolve(self._note_ports(out) if ok else [])
        self._run(['connect', 'list'], done, raw=True)

    def locate_board(self, board, found, missing):
        """``found(port)`` si la carte ``board`` est branchée, sinon ``missing()``."""
        def check(ports):
            for port, _, serial in ports:
                if safe_board_id(serial or port) == board:
                    found(port)
                    return
            missing()

        seen = self._ports_seen
        if seen is not None and time.monotonic() - seen[0] < PORTS_MAX_AGE:
            check(seen[1])
            return
        self._run(['connect', 'list'],
                  lambda ok, out, err: check(self._note_ports(out) if ok
                                             else []), raw=True)

    def open_cached(self, path):
        """
        Un fichier de carte (copie du cache) est demandé par les fichiers
        récents ou l'arborescence du PC : on ne l'ouvre pas tel quel, on relit
        le vrai fichier sur la carte, comme depuis la liste de la carte.

        :return: vrai si la demande est prise en charge ici
        """
        info = self.split_cache_path(path)
        if info is None:
            return False
        board, remote = info
        key = os.path.normcase(os.path.abspath(path))
        for editor in self._ui.tabWidgetEditors.widgets(include_clones=True):
            try:
                if os.path.normcase(os.path.abspath(editor.file.path)) == key:
                    return False        # déjà ouvert : simple mise au premier plan
            except (AttributeError, TypeError):
                continue
        self.locate_board(
            board, lambda port: self._switch_and_open(board, port, remote),
            lambda: self._error(
                tr('Board port'),
                tr('The board of this file is not connected.')))
        return True

    def _switch_and_open(self, board, port, remote):
        """Choisit la carte du fichier, affiche son contenu et ouvre le fichier."""
        Settings().micropython_port = port
        Settings().micropython_board = board
        self.show_board()
        self._open_remote(remote, board, port)

    def attach(self, editor, force=False):
        """
        Relie un éditeur à sa carte : chaque enregistrement d'une copie de
        fichier de la carte (ouverte depuis le panneau ou depuis les fichiers
        récents) est renvoyé sur la carte d'origine.
        """
        try:
            path = editor.file.path
        except AttributeError:
            return
        if getattr(editor, '_board_linked', False):
            return
        if not force and (not path or not self.is_board_file(path)):
            return
        editor._board_linked = True
        editor.text_saved.connect(
            lambda saved, ed=editor: self._on_saved(ed, saved))

    def open_remote(self, remote):
        self._with_board(
            lambda board, port: self._open_remote(remote, board, port))

    def _open_remote(self, remote, board, port):
        drive = self.drive_of(board)
        if drive:
            # lecteur monté : c'est un vrai fichier du PC, ouvert tel quel
            path = self.drive_path(drive, remote)
            if os.path.isfile(path):
                self.app.file.open_file(path)
            return
        local = self.local_path(remote, board)
        key = os.path.normcase(os.path.abspath(local))
        for editor in self._ui.tabWidgetEditors.widgets(include_clones=True):
            if os.path.normcase(os.path.abspath(editor.file.path)) == key:
                self.app.edit.add_editor(local)     # simple mise au premier plan
                self.app.view.show_edit_page()
                return
        os.makedirs(os.path.dirname(local), exist_ok=True)
        self._run(['cp', ':' + remote, local],
                  lambda ok, out, err: self._downloaded(
                      ok, err or out, remote, local, board), port=port)

    def _downloaded(self, ok, message, remote, local, board):
        if not ok or not os.path.isfile(local):
            self._error(tr('Cannot read the file from the board'), message)
            return
        editor = self.app.edit.add_editor(local)    # (relié par attach)
        if editor is not None:
            editor._board_synced = board        # contenu identique à la carte
        self.app.view.show_edit_page()

    def _on_saved(self, editor, path):
        """
        Envoie le fichier enregistré sur la carte actuellement branchée (à
        l'emplacement qu'il avait sur la carte d'origine), même si ce n'est
        plus la carte dont il provient.
        """
        info = self.split_cache_path(path)
        if info is None:
            return
        remote = info[1]
        self._pending += 1
        self._with_board(
            lambda board, port: self._upload(editor, path, remote, port,
                                             board),
            on_missing=lambda: self._not_sent(editor))

    def sync_then(self, editor, path, was_dirty, callback):
        """
        Appelle ``callback`` quand le fichier est sur la carte courante (par
        exemple pour l'exécuter) ; pas d'appel si l'envoi échoue.

        :param was_dirty: le fichier vient d'être enregistré parce qu'il était
                          modifié : son envoi est déjà en cours
        """
        if was_dirty:
            self._after.append(callback)        # l'envoi est déjà en cours
            return

        def decide(board, port):
            if getattr(editor, '_board_synced', None) == board:
                # inchangé et déjà sur cette carte : rien à envoyer
                if self._pending == 0:
                    callback()
                else:
                    self._after.append(callback)    # d'autres envois en cours
            else:
                self._on_saved(editor, path)
                self._after.append(callback)

        self._with_board(decide, on_missing=lambda: self._not_sent_run(editor))

    # --------------------------------------------- « Enregistrer sous » carte
    def save_as(self, editor, save_as_on_pc):
        """
        « Enregistrer sous » : si une carte est branchée, demande s'il faut
        enregistrer sur le PC ou sur la carte (le fichier devient alors
        celui de la carte).

        :param save_as_on_pc: fonction qui fait l'enregistrement habituel
        """
        from pyqode.cobol.widgets import CobolCodeEdit
        if isinstance(editor, CobolCodeEdit) or \
                self.controller.quiet_interpreter() is None:
            save_as_on_pc()
            return
        self._detect_board(
            lambda board, port: self._where(editor, board, port,
                                            save_as_on_pc),
            save_as_on_pc)

    def _detect_board(self, found, missing):
        """Cherche la carte branchée : found(identifiant, port) ou missing()."""
        from .micropython import parse_port_list
        chosen = Settings().micropython_port

        def done(ok, out, err):
            ports = parse_port_list(out if ok else '')
            if chosen:
                present = [p for p in ports if p[0] == chosen]
                if not present and self._adopt_only_board(ports):
                    present = ports
                ports = present
            if ports:
                port, _, serial = ports[0]
                found(safe_board_id(serial or port), port)
            else:
                missing()
        self._run(['connect', 'list'], done, raw=True)

    def _ask_destination(self, board):
        box = QtWidgets.QMessageBox(self.controller.main_window)
        box.setWindowTitle(tr('Save as'))
        box.setText(tr('Where do you want to save this file?'))
        box.setInformativeText(tr('A board is connected: "%s".') % board)
        pc = box.addButton(tr('On the PC'), QtWidgets.QMessageBox.AcceptRole)
        card = box.addButton(tr('On the board'),
                             QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(pc)
        box.exec_()
        if box.clickedButton() is pc:
            return 'pc'
        if box.clickedButton() is card:
            return 'board'
        return None

    def _ask_remote_path(self, default):
        text, ok = QtWidgets.QInputDialog.getText(
            self.controller.main_window, tr('Save on the board'),
            tr('Path of the file on the board:'),
            QtWidgets.QLineEdit.Normal, default)
        return text.strip() if ok else None

    def _confirm_replace(self, remote):
        return QtWidgets.QMessageBox.question(
            self.controller.main_window, tr('Save on the board'),
            tr('%s already exists on the board. Replace it?') % remote
        ) == QtWidgets.QMessageBox.Yes

    def _default_remote(self, editor):
        """Dossier sélectionné dans l'arborescence de la carte + nom."""
        folder = '/'
        item = self.tree.currentItem() if self.tree.isVisible() else None
        folder_item = self._folder_item(item) if item is not None else None
        if folder_item is not None and folder_item.data(0, ROLE_PATH):
            folder = folder_item.data(0, ROLE_PATH)
        return join_remote(folder, editor.file.name or 'main.py')

    def _where(self, editor, board, port, save_as_on_pc):
        choice = self._ask_destination(board)
        if choice == 'pc':
            save_as_on_pc()
        elif choice == 'board':
            remote = self._ask_remote_path(self._default_remote(editor))
            if not remote:
                return
            remote = '/' + '/'.join(p for p in remote.split('/') if p)
            if remote == '/' or chr(92) in remote:
                self._error(tr('Save on the board'), tr('Invalid path.'))
                return
            drive = self.drive_of(board)
            if drive:
                self._save_as_on_drive(editor, remote, drive)
                return
            self._run(['exec', CHECK_PATH_CODE % remote],
                      lambda ok, out, err: self._prepared(
                          ok, out, err, editor, remote, board), port=port)

    def _save_as_on_drive(self, editor, remote, drive):
        """Enregistre directement sur le lecteur de la carte."""
        target = self.drive_path(drive, remote)
        if os.path.exists(target) and not self._confirm_replace(remote):
            return
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            editor.file.save(target, encoding=editor.file.encoding or 'utf-8')
        except (OSError, UnicodeError) as e:
            self._error(tr('Save on the board'), str(e))
            return
        self._retitle(editor)
        self.app.file.recent_files_manager.open_file(target)

    def _retitle(self, editor):
        tabs = editor.parent_tab_widget
        tabs.setTabText(tabs.indexOf(editor), editor.file.name)
        self.controller.main_window.setWindowTitle(
            '%s - %s' % (editor.file.name, self.app.title))

    def _prepared(self, ok, out, err, editor, remote, board):
        if not ok:
            self._error(tr('Save on the board'), err or out)
            return
        if 'EXISTS' in out and not self._confirm_replace(remote):
            return
        self._convert(editor, remote, board)

    def _convert(self, editor, remote, board):
        """
        Le fichier devient celui de la carte : enregistré dans le cache, relié
        à la carte, puis envoyé (par le mécanisme habituel d'enregistrement).
        """
        local = self.local_path(remote, board)
        try:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            self.attach(editor, force=True)     # l'enregistrement envoie
            editor.file.save(local, encoding=editor.file.encoding or 'utf-8')
        except (OSError, UnicodeError) as e:
            self._error(tr('Save on the board'), str(e))
            return
        editor._board_synced = None     # ajusté par l'envoi
        self._retitle(editor)

    def _not_sent_run(self, editor):
        self._error(tr('Cannot save the file on the board'),
                    tr('No board detected. Nothing was sent: connect a board '
                       'and save again.'))

    def _end_upload(self, ok):
        self._pending -= 1
        self._batch_ok = self._batch_ok and ok
        if self._pending > 0:
            return
        self._pending = 0
        success, self._batch_ok = self._batch_ok, True
        callbacks, self._after = self._after, []
        if success:
            for callback in callbacks:
                callback()

    def _not_sent(self, editor):
        self._mark_unsaved(editor)
        self._error(
            tr('Cannot save the file on the board'),
            tr('No board detected. Nothing was sent: connect a board and '
               'save again.'))
        self._end_upload(False)

    @staticmethod
    def _mark_unsaved(editor):
        """L'onglet redevient « modifié » : la carte n'a pas la dernière version."""
        try:
            editor.document().setModified(True)
        except RuntimeError:
            pass        # onglet fermé entre-temps

    def _upload(self, editor, path, remote, port, board, attempt=0,
                created=False):
        def again():
            QtCore.QTimer.singleShot(
                READ_ONLY_RETRY_DELAY,
                lambda: self._upload(editor, path, remote, port, board,
                                     attempt + 1, created))

        def written(ok, out, err):
            message = err or out
            if not ok and is_read_only_error(message):
                if self._write_via_drive(path, remote, board):
                    self._uploaded(True, '', remote, editor, board)
                    return
                if attempt < READ_ONLY_RETRIES:
                    again()
                    return
            self._uploaded(ok, message, remote, editor, board)

        def done(ok, out, err):
            message = err or out
            if not ok and not created and 'Errno 2' in message:
                # absent de la carte (nouveau fichier, ou supprimé depuis)
                try:
                    with open(path, 'rb') as f:
                        data = f.read()
                except OSError:
                    data = None
                if data is not None and len(repr(data)) <= WRITE_NEW_MAX:
                    # création et contenu en une seule écriture
                    self._run(['exec', WRITE_NEW_CODE % (remote, data)],
                              written, port=port)
                else:
                    # gros fichier : fichier vide, attente du rechargement
                    # de la carte, puis copie
                    self._run(
                        ['exec', TOUCH_PATH_CODE % remote],
                        lambda ok2, out2, err2: QtCore.QTimer.singleShot(
                            AFTER_CREATE_DELAY,
                            lambda: self._upload(editor, path, remote, port,
                                                 board, attempt, True))
                        if ok2 else self._uploaded(
                            False, err2 or out2, remote, editor, board),
                        port=port)
                return
            written(ok, out, err)
        self._run(['cp', path, ':' + remote], done, port=port)

    def _find_folder_item(self, folder):
        """Le dossier ``folder`` de l'arborescence, ou le plus proche déjà lu."""
        while True:
            stack = [self.tree.topLevelItem(i)
                     for i in range(self.tree.topLevelItemCount())]
            while stack:
                item = stack.pop()
                if item.data(0, ROLE_DIR) and \
                        item.data(0, ROLE_PATH) == folder and \
                        item.data(0, ROLE_STATE) == 'loaded':
                    return item
                stack.extend(item.child(i) for i in range(item.childCount()))
            if folder in ('/', ''):
                return None
            folder = folder.rsplit('/', 1)[0] or '/'

    def _refresh_remote_folder(self, remote):
        """Relit le dossier de la carte qui contient ``remote``."""
        if not self.button.isChecked():
            return
        item = self._find_folder_item(remote.rsplit('/', 1)[0] or '/')
        if item is not None:
            self._reload(item)

    @staticmethod
    def _drive_target(board, remote):
        drive = find_circuitpython_drive(board)
        if drive is None:
            return None
        return os.path.join(drive, *[p for p in remote.split('/') if p])

    def _write_via_drive(self, path, remote, board):
        """
        Carte en lecture seule pour ses programmes : on copie le fichier sur
        son lecteur USB (comme le fait Thonny).
        """
        target = self._drive_target(board, remote)
        if target is None:
            return False
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
        except OSError:
            _logger().exception('cannot write on the drive')
            return False
        return True

    def _create_via_drive(self, remote, is_dir, board):
        target = self._drive_target(board, remote)
        if target is None:
            return False
        try:
            if is_dir:
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                open(target, 'ab').close()
        except OSError:
            _logger().exception('cannot create on the drive')
            return False
        return True

    def _uploaded(self, ok, message, remote, editor, board):
        if ok:
            editor._board_synced = board
            if not self._after:
                # (pas pendant une exécution : un seul accès à la carte)
                self._refresh_remote_folder(remote)
        else:
            self._mark_unsaved(editor)
            self._error(tr('Cannot save the file on the board'), message)
        self._end_upload(ok)

    # ------------------------------------------------------------- mpremote
    def _error(self, title, message):
        message = str(message).strip()[-1500:]
        if 'could not enter raw repl' in message:
            from .micropython import RAW_REPL_HINT
            message += '\n\n' + RAW_REPL_HINT()
        elif is_read_only_error(message):
            message += '\n\n' + tr(
                'The board cannot be written by its own programs because the '
                'computer has mounted its USB drive (CIRCUITPY), and this '
                'drive was not found. Check that it is visible in the file '
                'explorer, or add these two lines to the board\'s boot.py '
                '(the computer will then see the drive as read-only): '
                'import storage / storage.remount("/", readonly=False)')
        elif 'failed to access' in message:
            message += '\n\n' + tr(
                'The board port cannot be opened. Another program (Thonny, a '
                'terminal...) may be using it, or the board has been '
                'replaced: choose its port in the MicroPython menu.')
        QtWidgets.QMessageBox.warning(
            self.controller.main_window, title, message)

    def shutdown(self):
        """À la fermeture de l'IDE : plus de commande en attente."""
        self._timer.stop()
        # les envois en cours doivent aboutir avant l'effacement du cache
        deadline = time.monotonic() + 5
        while (self._pending > 0 or self._job is not None) and \
                time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.02)
        self._queue.clear()
        job = self._job
        if job is not None:
            if not job.wait(3000):
                job.terminate()
                job.wait(500)
        # les copies de travail ne survivent pas à la session
        shutil.rmtree(self.cache_dir(), ignore_errors=True)

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
        job.start()

    def _finished(self, callback, code, out, err):
        job, self._job = self._job, None
        if job is not None:
            job.wait()
        _logger().debug('mpremote -> %s %r %r', code, out[:200], err[:200])
        try:
            callback(code == 0, out, err)
        finally:
            self._next()
