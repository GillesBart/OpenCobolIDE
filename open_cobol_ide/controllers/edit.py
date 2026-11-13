"""
Contains the EditController.

"""
import logging
import os
import weakref
from pyqode.core.api import TextHelper
from pyqode.core.panels import EncodingPanel
from pyqode.core.cache import Cache
from pyqode.qt import QtCore, QtGui, QtWidgets
from .base import Controller
from open_cobol_ide.translation import tr
from ..view.dialogs.preferences import DlgPreferences
from ..settings import Settings
from ..view.editors import CobolCodeEdit, GenericCodeEdit, TextEdit, \
    PythonCodeEdit, python_source_encoding, \
    update_editor_settings
from ..view.widgets import FSContextMenu


def _logger():
    return logging.getLogger(__name__)


class EditController(Controller):
    """
    Controls the edit view (and the edit menu).
    """
    #: the list of supported editor types
    editor_types = [CobolCodeEdit, PythonCodeEdit, GenericCodeEdit, TextEdit]

    @property
    def current_editor(self):
        """
        :rtype: pyqode.cobol.widgets.CobolCodeEdit
        """
        return self.ui.tabWidgetEditors.current_widget()

    def __init__(self, app):
        super().__init__(app)
        self.ui.tabWidgetEditors.editors.clear()
        self.ui.tabWidgetEditors.fallback_editor = GenericCodeEdit
        for editor_type in self.editor_types:
            self.ui.tabWidgetEditors.register_code_edit(editor_type)
        self.ui.tabWidgetEditors.current_changed.connect(
            self._current_changed)
        self.ui.tabWidgetEditors.last_tab_closed.connect(
            self._on_last_tab_closed)
        self._replacing_editor = False
        self.ui.tabWidgetEditors.document_saved.connect(
            self._on_document_saved)
        self.ui.tableWidgetOffsets.show_requested.connect(
            self.ui.dockWidgetOffsets.show)
        self.ui.actionPreferences.triggered.connect(self.edit_preferences)
        self.ui.btPreferences.clicked.connect(self.edit_preferences)
        self.ui.actionPreferences.setShortcut('Ctrl+P')
        # F2 / F6 / F9 : afficher ou masquer les panneaux « Système de
        # fichiers », « Navigation » et « Journaux » (F3/F4 restent à la
        # recherche). Ce sont les actions du menu Affichage > Fenêtres : la
        # touche s'affiche en regard de chaque commande.
        for dock, key in ((self.ui.dockWidgetFileSystem, 'F2'),
                          (self.ui.dockWidgetNavPanel, 'F6'),
                          (self.ui.dockWidgetLogs, 'F9')):
            action = dock.toggleViewAction()
            action.setShortcut(QtGui.QKeySequence(key))
            action.setShortcutContext(QtCore.Qt.ApplicationShortcut)
        self._setup_status_bar()
        self.ui.tvFileSystem.activated.connect(self._on_tvFileSystem_activated)
        self.ui.tvFileSystem.setHeaderHidden(True)
        self.ui.bt_fs_up.setIcon(QtGui.QIcon.fromTheme(
            'go-up', QtGui.QIcon(':/pyqode-icons/rc/go-up.png')))
        self.ui.bt_fs_up.clicked.connect(self._fs_go_up)
        for i in range(1, 4):
            self.ui.tvFileSystem.hideColumn(i)
        self.ui.tvFileSystem.add_ignore_patterns('bin', '.oci*.cbl')
        mnu = FSContextMenu(self.app)
        self.ui.tvFileSystem.set_context_menu(mnu)
        self.ui.tvFileSystem.file_created.connect(self.app.file.open_file)
        self.ui.tvFileSystem.file_renamed.connect(
            self.ui.tabWidgetEditors.rename_document)
        self.ui.tvFileSystem.file_deleted.connect(
            self.ui.tabWidgetEditors.close_document)
        lock_fs_path = Settings().lock_fs_path
        if lock_fs_path and os.path.exists(lock_fs_path):
            self.ui.tvFileSystem.set_root_path(lock_fs_path)
        else:
            Settings().lock_fs_path = ''
        self.ui.btFSLock.setChecked(lock_fs_path != '')
        self.ui.btFSLock.toggled.connect(self._on_fs_path_lock_toggled)
        self.ui.btNavLock.toggled.connect(self._on_navlock_toggled)
        self.ui.btNavLock.setChecked(not self.ui.twNavigation.sync_with_editor)
        self.ui.twNavigation.sync_with_editor_changed.connect(
            self._on_nav_sync_changed)
        self.ui.actionEnableLinter.toggled.connect(
            self._on_enable_linter_toggled)

    def _on_fs_path_lock_toggled(self, checked):
        if checked:
            Settings().lock_fs_path = \
                self.ui.tabWidgetEditors.current_widget().file.path
        else:
            Settings().lock_fs_path = ''

    def _on_nav_sync_changed(self, sync):
        self.ui.btNavLock.setChecked(not sync)

    def _on_navlock_toggled(self, checked):
        self.ui.btNavLock.blockSignals(True)
        self.ui.twNavigation.sync_with_editor = not checked
        self.ui.btNavLock.blockSignals(False)

    def _on_tvFileSystem_activated(self, index):
        path = self.ui.tvFileSystem.filePath(index)
        if os.path.isfile(path):
            self.app.file.open_file(path)

    def _setup_status_bar(self):
        """
        Adds some labels to the tool bar:
            - format (free or fixed)
            - encoding
            - cursor position (line:column)
        """
        # encoding
        self._lbl_encoding = QtWidgets.QLabel()
        self._lbl_encoding.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._lbl_encoding.setAlignment(
            QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        self._lbl_encoding.setText('UTF-8')
        # format
        self._cb_free_format = QtWidgets.QCheckBox(tr('Free format'))
        self._cb_free_format.setChecked(Settings().free_format)
        self._cb_free_format.toggled.connect(self._on_free_format_toggled)
        # cursor position
        self._lbl_cursor = QtWidgets.QLabel()
        self._lbl_cursor.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._lbl_cursor.setAlignment(QtCore.Qt.AlignHCenter |
                                      QtCore.Qt.AlignVCenter)
        self._lbl_cursor.setText('1:1')

        # path
        self._lbl_path = QtWidgets.QLabel()
        self._lbl_path.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._lbl_path.setText('n/a')

        # linter control button
        linter_btn = QtWidgets.QToolButton(self.ui.statusbar)
        linter_btn.setDefaultAction(self.ui.actionEnableLinter)

        self.ui.statusbar.addWidget(self._lbl_path, True)
        self.ui.statusbar.addAction(self.ui.actionEnableLinter)
        self.ui.statusbar.addPermanentWidget(linter_btn)
        self.ui.statusbar.addPermanentWidget(self._cb_free_format)
        self.ui.statusbar.addPermanentWidget(self._lbl_cursor)
        self.ui.statusbar.addPermanentWidget(self._lbl_encoding)
        self.ui.statusbar.setStyleSheet(
            'QStatusBar::item { border: 0px solid black };')

    def add_editor(self, path):
        """
        Adds an editor widget to the main tab widget.

        :param path: path of the file to open
        :param name: name of the tab
        :param mimetype: mimetype of the file to open.
        :return:
        """
        # fichier source Python : UTF-8 (ou BOM / cookie "# coding:") et non
        # l'encodage de la machine (cp1252), y compris si un ancien encodage a
        # été mémorisé pour ce fichier
        encoding = None
        if os.path.splitext(path)[1].lower() in PythonCodeEdit.extensions:
            encoding = python_source_encoding(path)
            if encoding:
                Cache().set_file_encoding(path, encoding)
        try:
            try:
                editor = self.ui.tabWidgetEditors.open_document(
                    path, encoding=encoding,
                    preferred_eol=Settings().preferred_eol,
                    autodetect_eol=Settings().autodetect_eol)
            except TypeError:
                editor = self.ui.tabWidgetEditors.open_document(
                    path, encoding=encoding)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self.main_window, tr('Failed to open path'),
                tr('Failed to open path: %s') % e)
        else:
            try:
                fw = editor.modes.get('FileWatcherMode')
            except KeyError:
                pass
            else:
                fw.file_deleted.connect(self._on_file_deleted)
            editor.app = weakref.ref(self.app)
            update_editor_settings(editor)
            # fichier illisible (encodage) puis « Annuler » : l'onglet se ferme
            # sans rien enregistrer
            try:
                editor.panels.get(EncodingPanel).cancel_requested.connect(
                    self._on_open_cancelled)
            except KeyError:
                pass
            # copie d'un fichier d'une carte MicroPython : renvoyée sur la
            # carte à chaque enregistrement
            micropython = getattr(self.app, 'micropython', None)
            if micropython is not None:
                micropython.board_files.attach(editor)
            try:
                self.ui.actionEnableLinter.blockSignals(True)
                self.ui.actionEnableLinter.setChecked(Settings().show_errors)
                self.ui.actionEnableLinter.blockSignals(False)
                editor.set_buttons()
            except AttributeError:
                pass
            self.app.cobol.display_file_type(editor)
            editor.cursorPositionChanged.connect(self._update_status_bar_labels)
            self._update_status_bar_labels()
            return editor

    def _on_open_cancelled(self, editor):
        """
        L'ouverture a échoué (caractères invalides) et l'utilisateur annule :
        on ferme l'onglet, sans enregistrer, sans toucher au fichier.
        """
        path = editor.file.path
        editor.document().setModified(False)
        QtCore.QTimer.singleShot(
            0, lambda: self.ui.tabWidgetEditors.close_document(path))

    def _pc_root(self):
        micropython = getattr(self.app, 'micropython', None)
        if micropython is not None:
            return micropython.board_files.pc_root()
        return Settings().last_path

    def new_document(self):
        """
        Ouvre un onglet vide « nouveau.txt », sans créer de fichier : le nom
        n'est demandé qu'à l'enregistrement.
        """
        tabs = self.ui.tabWidgetEditors
        editor = tabs.create_new_document(
            base_name='nouveau', extension='.txt',
            preferred_eol=Settings().preferred_eol,
            autodetect_eol=Settings().autodetect_eol)
        editor.app = weakref.ref(self.app)
        update_editor_settings(editor)
        if not self.ui.btFSLock.isChecked():
            # arborescence du PC : dernier dossier utilisé (sans fichier ouvert,
            # elle n'aurait sinon aucune racine)
            self.ui.tvFileSystem.set_root_path(self._pc_root())
        name = 'nouveau.txt'
        editor.setDocumentTitle(name)
        tabs_widget = editor.parent_tab_widget
        tabs_widget.setTabText(tabs_widget.indexOf(editor), name)
        self.app.view.show_edit_page()
        return editor

    def _on_document_saved(self, path, old_content):
        """
        Après un enregistrement : si l'extension du fichier ne correspond plus
        au type de l'éditeur (nouveau.txt enregistré en .py ou .cob), on
        remplace l'onglet par un éditeur du bon type (coloration, menus...).
        """
        tabs = self.ui.tabWidgetEditors
        for editor in tabs.widgets(include_clones=False):
            if editor.dirty or editor.clones:
                continue
            file_path = editor.file.path
            if not file_path or not os.path.exists(file_path):
                continue
            expected = tabs.editors.get(
                tabs.guess_mimetype(file_path), tabs.fallback_editor)
            if type(editor) is not expected:
                QtCore.QTimer.singleShot(
                    0, lambda e=editor: self._replace_editor(e))
                return

    def _replace_editor(self, editor):
        """
        Remplace l'éditeur sans rafraîchir la fenêtre pendant l'opération
        (évite que l'onglet « clignote »).
        """
        window = self.main_window
        window.setUpdatesEnabled(False)
        try:
            self._do_replace_editor(editor)
        finally:
            window.setUpdatesEnabled(True)

    def _do_replace_editor(self, editor):
        """
        Ferme l'onglet (déjà enregistré) et le rouvre avec l'éditeur adapté à
        son extension, au même endroit, avec le même curseur.
        """
        tabs = self.ui.tabWidgetEditors
        try:
            path = editor.file.path
            tw = editor.parent_tab_widget
            index = tw.indexOf(editor)
            position = editor.textCursor().position()
            scroll = editor.verticalScrollBar().value()
            encoding = editor.file.encoding
        except (AttributeError, RuntimeError):
            return
        if editor.dirty or index < 0:
            return
        if os.path.splitext(path)[1].lower() not in PythonCodeEdit.extensions:
            Cache().set_file_encoding(path, encoding)
        self._replacing_editor = True
        try:
            tabs.close_document(path)
            new = self.add_editor(path)
        finally:
            self._replacing_editor = False
        if new is None:
            return
        try:
            if new.parent_tab_widget is tw:
                tw.tabBar().moveTab(tw.indexOf(new), index)
            cursor = new.textCursor()
            cursor.setPosition(min(position, new.document().characterCount() - 1))
            new.setTextCursor(cursor)
            new.verticalScrollBar().setValue(scroll)
            new.parent_tab_widget.setCurrentWidget(new)
            new.setFocus()
        except (AttributeError, RuntimeError):
            pass

    def _on_last_tab_closed(self):
        if self._replacing_editor:
            return          # remplacement d'éditeur : l'onglet est rouvert
        self.main_window.save_state()
        self.main_window.setWindowTitle(self.app.title)
        self.ui.twNavigation.set_editor(None)
        if getattr(self.main_window, 'closing', False):
            return          # fermeture de l'IDE : rien à afficher
        if Settings().show_home_page:
            self.app.view.show_home_page()
        else:
            self.new_document()

    def _current_changed(self, new_index):
        """
        Updates ui when the current editor changed.

        :param new_index: index of the new current editor.
        """
        if new_index != -1:
            editor = self.ui.tabWidgetEditors.current_widget()
            self.main_window.setWindowTitle(
                '%s - %s' % (editor.file.name, self.app.title))
            # update tools (outline and offsets table)
            self.ui.twNavigation.set_editor(editor)
            if self.ui.btFSLock.isChecked() and \
                    not os.path.exists(Settings().lock_fs_path):
                Settings().lock_fs_path = ''
                self.ui.btFSLock.setChecked(False)
            # (une copie de fichier de carte ne doit pas faire pointer
            # l'arborescence du PC vers le dossier de cache)
            micropython = getattr(self.app, 'micropython', None)
            on_board = micropython is not None and \
                micropython.board_files.on_board_storage(editor.file.path)
            if not self.ui.btFSLock.isChecked() and not on_board:
                path = editor.file.path or ''
                # un document pas encore enregistré (« nouveau.txt ») n'a pas
                # de dossier : on garde le dernier dossier utilisé
                new_root = os.path.dirname(path) if os.path.isabs(path) \
                    else self._pc_root()
                if not os.path.isdir(new_root):
                    new_root = self._pc_root()
                new_root = os.path.normpath(new_root)
                if new_root != self.ui.tvFileSystem.root_path:
                    self.ui.tvFileSystem.set_root_path(new_root)
            self.ui.tableWidgetOffsets.set_editor(editor)
            # update linter action
            try:
                self.ui.actionEnableLinter.blockSignals(True)
                self.ui.actionEnableLinter.setChecked(
                    editor.linter_mode.enabled)
                self.ui.actionEnableLinter.blockSignals(False)
            except AttributeError:
                # not a cobol code edit
                self.ui.actionEnableLinter.setEnabled(False)
            else:
                self.ui.actionEnableLinter.setEnabled(True)
            # update current editor menu (actions placed directly in Edit)
            _fill_edit_menu(self.ui, editor)

            # update file type menu
            self.app.cobol.display_file_type(editor)
            self._update_python_menu(editor)
            # update cobol related actions (run/compile
            if isinstance(editor, PythonCodeEdit):
                # script Python : pas de compilation, mais on peut l'exécuter
                self.ui.menuCobol.setEnabled(False)
                self.app.cobol.enable_compile(False)
                self.app.cobol.enable_run(
                    not self.ui.consoleOutput.is_running)
                self._update_status_bar_labels()
                _logger().debug('current editor changed: %s',
                                editor.file.path)
                return
            try:
                # lève AttributeError si ce n'est pas un éditeur COBOL
                self.app.edit.current_editor.file_type
            except (AttributeError, UnicodeDecodeError):
                # not a cobol editor, or not file not loaded due to an
                # encoding error
                self.ui.menuCobol.setEnabled(False)
                self.app.cobol.enable_compile(False)
                self.app.cobol.enable_run(False)
            else:
                self.ui.menuCobol.setEnabled(True)
                self.app.cobol.enable_compile(
                    not self.ui.consoleOutput.is_running)
                self.app.cobol.enable_run(
                    not self.ui.consoleOutput.is_running)
            self._update_status_bar_labels()
            _logger().debug('current editor changed: %s', editor.file.path)
        else:
            self.ui.actionEnableLinter.setEnabled(False)
            self._update_python_menu(None)

    def _update_python_menu(self, editor):
        """
        Le menu Python (et MicroPython : port de la carte, fichiers de la
        carte...) est actif partout sauf sur un fichier COBOL.
        """
        python = getattr(self.app, 'python', None)
        if python is not None:
            python.menu.menuAction().setEnabled(
                not isinstance(editor, CobolCodeEdit))

    def _get_cursor_pos_in_bytes(self, original, encoding):
        text = TextHelper(self.current_editor).current_line_text()
        text = text[:original]
        try:
            return len(bytes(text, encoding))
        except UnicodeEncodeError:
            return original

    def _update_status_bar_labels(self):
        """
        Updates the status bar labels (format, encoding, cursor position).
        """
        if self.current_editor:
            l, c = TextHelper(self.current_editor).cursor_position()
            encoding = self.current_editor.file.encoding
            if Settings().show_cursor_pos_in_bytes:
                c = self._get_cursor_pos_in_bytes(c, encoding)
            self._lbl_cursor.setText('%d:%d' % (l + 1, c + 1))
            self._lbl_encoding.setText(encoding)
            self._cb_free_format.setEnabled(True)
            self._cb_free_format.setChecked(Settings().free_format)
            self._lbl_path.setText(self.current_editor.file.path)
        else:
            self._lbl_cursor.setText('n/a')
            self._lbl_encoding.setText('n/a')
            self._cb_free_format.setChecked(False)
            self._cb_free_format.setEnabled(False)
            self._lbl_path.setText('n/a')

    def edit_preferences(self):
        try:
            DlgPreferences.edit_preferences(self.main_window)
        except ValueError:
            # dialog canceled
            _logger().info('settings dialog canceled')
        else:
            _logger().info('applying settings')
            self.app.apply_mimetypes_preferences()
            self.app.update_app_style()
            self.app.home.update_style()
            QtGui.QIcon.setThemeName(Settings().icon_theme)
            self._update_status_bar_labels()
            for editor in self.ui.tabWidgetEditors.widgets(
                    include_clones=True):
                update_editor_settings(editor)
                editor.rehighlight()
                try:
                    self.ui.actionEnableLinter.blockSignals(True)
                    self.ui.actionEnableLinter.setChecked(
                        editor.linter_mode.enabled)
                    self.ui.actionEnableLinter.blockSignals(False)
                except AttributeError:
                    # not a cobol code edit
                    self.ui.actionEnableLinter.setEnabled(False)
                else:
                    self.ui.actionEnableLinter.setEnabled(True)

    def _on_file_deleted(self, editor):
        """
        Le fichier a disparu (carte ou clef USB débranchée, fichier supprimé) :
        l'onglet reste ouvert, avec son contenu ; on ne peut plus qu'
        « Enregistrer sous ».
        """
        _logger().debug('file deleted: %s', editor.file.path)

    def _fs_go_up(self):
        """
        Go up in the FileSystem view
        """
        parent_path = os.path.abspath(
            os.path.join(self.ui.tvFileSystem.root_path, os.path.pardir))
        self.ui.tvFileSystem.set_root_path(parent_path)

    def _on_enable_linter_toggled(self, is_checked):
        self.current_editor.linter_mode.enabled = is_checked

    def _on_free_format_toggled(self, state):
        Settings().free_format = state
        for editor in self.ui.tabWidgetEditors.widgets(include_clones=True):
            update_editor_settings(editor)


def _fill_edit_menu(ui, editor):
    """
    Place les actions de l'editeur actif, puis celles de son sous-menu
    'Advanced', directement dans le menu Edit (avant Preferences).
    """
    menu = ui.menuEdit
    menu.removeAction(ui.mnuActiveEditor.menuAction())
    for a in getattr(ui, '_edit_dynamic', []):
        try:
            menu.removeAction(a)
        except RuntimeError:
            # action ou menu détruit avec son éditeur (onglet fermé) :
            # Qt l'a déjà retiré du menu
            pass
    acts = menu.actions()
    pref = acts.index(ui.actionPreferences)
    anchor = ui.actionPreferences
    if pref and acts[pref - 1].isSeparator():
        anchor = acts[pref - 1]
    dynamic = []

    def add(action):
        menu.insertAction(anchor, action)
        dynamic.append(action)

    def new_group():
        if dynamic and not dynamic[-1].isSeparator():
            dynamic.append(menu.insertSeparator(anchor))

    for a in editor.actions():
        if not hasattr(a, 'dont_show_in_edit_menu'):
            add(a)
    advanced = getattr(editor, '_sub_menus', {}).get('Advanced')
    others = [m for m in getattr(editor, '_menus', []) if m is not advanced]
    if advanced is not None and advanced.actions():
        new_group()
        for a in advanced.actions():
            add(a)
    if others:
        new_group()
        for m in others:
            menu.insertMenu(anchor, m)
            dynamic.append(m.menuAction())
    ui._edit_dynamic = dynamic
