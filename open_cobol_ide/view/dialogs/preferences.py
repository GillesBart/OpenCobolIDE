import os
import sys
from pyqode.core.api.syntax_highlighter import PYGMENTS_STYLES, ColorScheme
from pyqode.qt import QtCore, QtGui, QtWidgets
from open_cobol_ide.translation import tr
from open_cobol_ide import system, compilers
from open_cobol_ide.enums import GnuCobolStandard
from open_cobol_ide.settings import Settings
from open_cobol_ide.view.editors import GenericCodeEdit, PythonCodeEdit
from open_cobol_ide.view.forms import dlg_preferences_ui
from open_cobol_ide.view.dialogs.check_compiler import DlgCheckCompiler
from open_cobol_ide.view.dialogs.cobc_help import DlgCobcHelp

OTHER_TEMPLATE = '''{
    "nom": "ma carte",
    "version": 2,
    "actif": true,
    "broches": [2, 4, 5],
    "delai_ms": 250
}
'''

PYTHON_TEMPLATE = '''import machine
from time import sleep_ms


class Led:
    """Clignotement d'une LED."""
    PIN = 2

    def __init__(self, pin=PIN):
        self.pin = machine.Pin(pin, machine.Pin.OUT)

    def blink(self, times=3):
        # alterne l'état de la broche
        for i in range(times):
            self.pin.value(not self.pin.value())
            sleep_ms(250)
        return "fini : %d fois" % times


if __name__ == "__main__":
    Led().blink(5)
'''

DEFAULT_TEMPLATE = '''      * Author:
      * Date:
      * Purpose:
      * Tectonics: cobc
       IDENTIFICATION DIVISION.
      *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
       PROGRAM-ID. YOUR-PROGRAM-NAME.
       ENVIRONMENT DIVISION.
      *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
       CONFIGURATION SECTION.
      *-----------------------
       INPUT-OUTPUT SECTION.
      *-----------------------
       DATA DIVISION.
      *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
       FILE SECTION.
      *-----------------------
       WORKING-STORAGE SECTION.
      *-----------------------
       PROCEDURE DIVISION.
      *-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
       MAIN-PROCEDURE.
      **
      * The main procedure of the program
      **
            DISPLAY "Hello world"
            STOP RUN.
      ** add other procedures here
       END PROGRAM YOUR-PROGRAM-NAME.

'''


class DlgPreferences(QtWidgets.QDialog, dlg_preferences_ui.Ui_Dialog):
    flags_in_checkbox = [
        '-g', '-ftrace', '-ftraceall', '-fdebugging-line', '-static', '-debug', '-W', '-Wall'
    ]

    def __init__(self, parent):
        super().__init__(parent, QtCore.Qt.WindowSystemMenuHint |
                         QtCore.Qt.WindowTitleHint |
                         QtCore.Qt.WindowCloseButtonHint)
        self.setupUi(self)
        # démarrage : page d'accueil ou onglet vide
        self.checkBoxShowHome = QtWidgets.QCheckBox(self.groupBox_3)
        self.formLayout.addRow(tr('Show the home page at startup'),
                               self.checkBoxShowHome)
        self._help_dlg = None
        themes = system.icon_themes()
        if themes:
            self.comboBoxIconTheme.addItems(themes)
        else:
            self.comboBoxIconTheme.hide()
            self.lblIconTheme.hide()
        self.tabWidget.setTabIcon(0, QtGui.QIcon.fromTheme(
            'accessories-text-editor',
            QtGui.QIcon(':/ide-icons/rc/cobol-mimetype.png')))
        theme = 'application-x-executable'
        if QtGui.QIcon.hasThemeIcon('run-build'):
            theme = 'run-build'
        self.tabWidget.setTabIcon(2, QtGui.QIcon.fromTheme(
            theme, QtGui.QIcon(':/ide-icons/rc/application-x-executable.png')))
        self.tabWidget.setTabIcon(1, QtGui.QIcon.fromTheme(
            'applications-graphics',
            QtGui.QIcon(':/ide-icons/rc/applications-graphics.png')))
        self.tabWidget.setTabIcon(3, QtGui.QIcon.fromTheme(
            'media-playback-start', QtGui.QIcon(
                ':/ide-icons/rc/media-playback-start.png')))
        icon_add = QtGui.QIcon.fromTheme(
            'list-add', QtGui.QIcon(':/ide-icons/rc/list-add.png'))
        icon_remove = QtGui.QIcon.fromTheme(
            'list-remove', QtGui.QIcon(':/ide-icons/rc/list-remove.png'))
        icon_open_folder = QtGui.QIcon.fromTheme(
            'folder-open', QtGui.QIcon(':/ide-icons/rc/document-open.png'))
        icon_clear = QtGui.QIcon.fromTheme(
            'edit-clear', QtGui.QIcon(':/ide-icons/rc/edit-clear.png'))
        self.bt_add_run_env.setIcon(icon_add)
        self.bt_rm_run_env.setIcon(icon_remove)
        self.bt_clear_run_env.setIcon(icon_clear)
        self.btAddAbsoluteCopyPath.setIcon(icon_open_folder)
        self.btAddRelativeCopyPath.setIcon(icon_add)
        self.btRemoveCopyPath.setIcon(icon_remove)
        self.toolButtonAddLibPath.setIcon(icon_open_folder)
        self.toolButtonAddRelativeLibPath.setIcon(icon_add)
        self.toolButtonRemoveLibPath.setIcon(icon_remove)
        self.toolButtonCheckCompiler.setIcon(QtGui.QIcon.fromTheme(
            'emblem-checked',
            QtGui.QIcon(':/ide-icons/rc/emblem-checked.png')))

        self.buttonBox.button(self.buttonBox.Reset).clicked.connect(self.reset)
        self.buttonBox.button(self.buttonBox.RestoreDefaults).clicked.connect(
            self.restore_defaults)
        self.checkBoxRunExtTerm.toggled.connect(
            self.lineEditRunTerm.setEnabled)
        self.listWidgetColorSchemes.currentItemChanged.connect(
            self.update_color_scheme_preview)
        self.plainTextEdit.setPlainText(DEFAULT_TEMPLATE, '', '')
        self._setup_language_color_selector()
        self.lineEditDbpre.setReadOnly(True)
        self.lineEditDbpreFramework.setReadOnly(True)
        self.lineEditCobmysqlapi.setReadOnly(True)
        self.toolButtonDbpre.clicked.connect(self._select_dbpre)
        self.toolButtonDbpreFramework.clicked.connect(
            self._select_dbpre_framework)
        self.toolButtonCobMySqlApiPath.clicked.connect(
            self._select_cobmysqlapi)
        self.checkBoxShowDbPass.stateChanged.connect(
            self._on_show_pass_state_changed)
        self.toolButtonVCVARS.clicked.connect(self._select_vcvarsall)
        self.toolButtonCustomCompilerPath.clicked.connect(
            self._select_custom_compiler_path)
        self.toolButtonAddLibPath.clicked.connect(self._add_lib_path)
        self.toolButtonAddRelativeLibPath.clicked.connect(
            self._add_rel_lib_path)
        self.toolButtonRemoveLibPath.clicked.connect(self._rm_lib_path)
        self.btAddAbsoluteCopyPath.clicked.connect(self._add_copy_path)
        self.btAddRelativeCopyPath.clicked.connect(
            self._add_rel_copy_path)
        self.btRemoveCopyPath.clicked.connect(self._rm_copy_path)
        self.toolButtonESQLOC.clicked.connect(self._select_esqloc)
        self.btCompilerFlagsHelp.clicked.connect(self._show_gnu_cobol_help)
        if not system.windows:
            self.labelVCVARS.hide()
            self.lineEditVCVARS.hide()
            self.toolButtonVCVARS.hide()
            self.combo_arch.hide()
            self.stackedWidgetSQL.setCurrentIndex(0)
        else:
            self.stackedWidgetSQL.setCurrentIndex(1)
        self.toolButtonCheckCompiler.clicked.connect(self._check_compiler)
        self.cbPATH.stateChanged.connect(self.PATH.setEnabled)
        self.cbCOB_CONFIG_DIR.stateChanged.connect(
            self.COB_CONFIG_DIR.setEnabled)
        self.cbCOB_COPY_DIR.stateChanged.connect(self.COB_COPY_DIR.setEnabled)
        self.cbCOB_INCLUDE_PATH.stateChanged.connect(
            self.COB_INCLUDE_PATH.setEnabled)
        self.cbCOB_LIB_PATH.stateChanged.connect(self.COB_LIB_PATH.setEnabled)

        self.PATH.setEnabled(self.cbPATH.isChecked())
        self.COB_CONFIG_DIR.setEnabled(self.cbCOB_CONFIG_DIR.isChecked())
        self.COB_COPY_DIR.setEnabled(self.cbCOB_COPY_DIR.isChecked())
        self.COB_INCLUDE_PATH.setEnabled(self.cbCOB_INCLUDE_PATH.isChecked())
        self.COB_LIB_PATH.setEnabled(self.cbCOB_LIB_PATH.isChecked())

        self.bt_add_run_env.clicked.connect(self._add_run_env_variable)
        self.bt_rm_run_env.clicked.connect(self._rm_run_env_variable)
        self.bt_clear_run_env.clicked.connect(self._clear_run_env)

        self._margin_spin_boxes = [
            self.spin_box_margin_1,
            self.spin_box_margin_2,
            self.spin_box_margin_3,
            self.spin_box_margin_4
        ]

        self._margin_color_pickers = [
            self.color_picker_1,
            self.color_picker_2,
            self.color_picker_3,
            self.color_picker_4
        ]

        self.bt_working_dir.clicked.connect(self._select_working_dir)

        self.initial_settings = Settings().export_to_dict()
        self.reset(all_tabs=True)

    def _select_working_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('Select working directory'))
        if path:
            self.edit_working_dir.setText(path)

    def _check_compiler(self, *_):
        self.apply()
        pth = self.lineEditCompilerPath.text()
        if os.path.exists(pth) or system.which(pth) is not None:
            DlgCheckCompiler.check(self, pth)
        else:
            QtWidgets.QMessageBox.warning(
                self, tr('Invalid compiler path'),
                tr('Not a valid compiler path, path does not exists: %s!') % pth)

    def _add_lib_path(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('Select a library directory'))
        if path:
            self.listWidgetLibPaths.addItem(system.normpath(path))

    def _add_rel_lib_path(self):
        path, status = QtWidgets.QInputDialog.getText(
            self, tr('Add relative library path'), tr('Path:'))
        if status:
            self.listWidgetLibPaths.addItem(system.normpath(path))

    def _rm_lib_path(self):
        items = self.listWidgetLibPaths.selectedItems()
        for item in items:
            self.listWidgetLibPaths.takeItem(self.listWidgetLibPaths.row(item))

    def _add_copy_path(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('Select a copybook path'))
        if path:
            self.listWidgetCopyPaths.addItem(system.normpath(path))

    def _add_rel_copy_path(self):
        path, status = QtWidgets.QInputDialog.getText(
            self, tr('Add relative copybook path'), tr('Path:'))
        if status:
            self.listWidgetCopyPaths.addItem(system.normpath(path))

    def _rm_copy_path(self):
        items = self.listWidgetCopyPaths.selectedItems()
        for item in items:
            self.listWidgetCopyPaths.takeItem(
                self.listWidgetCopyPaths.row(item))

    def _select_vcvarsall(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, tr('Select vcvarsall.bat'), self.lineEditVCVARS.text())
        if path:
            path = system.normpath(path)
            if os.path.isfile(path) and path.lower().endswith('vcvarsall.bat'):
                self.lineEditVCVARS.setText(path)
            else:
                QtWidgets.QMessageBox.warning(
                    self, tr("Invalid vcvarsall batch path"),
                    tr("%r is not a valid vcvarsall batch file") % path)

    def _select_custom_compiler_path(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, tr('Select path to a GnuCOBOL compiler executable'),
            self.lineEditCompilerPath.text())
        if path:
            self.lineEditCompilerPath.setText(system.normpath(path))
            self._check_compiler()

    def _show_gnu_cobol_help(self):
        if self._help_dlg is None or not self._help_dlg.isVisible():
            help_text = compilers.GnuCobolCompiler.get_cobc_help()
            self._help_dlg = DlgCobcHelp(self, help_text)
            self._help_dlg.show()

    def _select_esqloc(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('Select esqlOC folder'), self.lineEditESQLOC.text())
        if path:
            self.lineEditESQLOC.setText(system.normpath(path))

    def _select_dbpre(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, tr('Select dbpre executable'), self.lineEditDbpre.text())
        if path:
            self.lineEditDbpre.setText(system.normpath(path))
            self.labelDbpreVersion.setText(
                compilers.DbpreCompiler(path).get_version()
                if Settings().dbpre != '' else ''
            )

    def _select_cobmysqlapi(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, tr('Select cobmysqlapi object file'),
            self.lineEditCobmysqlapi.text())
        if path:
            self.lineEditCobmysqlapi.setText(system.normpath(path))

    def _select_dbpre_framework(self):
        def bool_to_string(value):
            if value:
                return 'Found'
            else:
                return 'Missing'

        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('Select dbpre framework directory'),
            self.lineEditDbpreFramework.text())
        if path:
            path = system.normpath(path)
            pgctbbat = os.path.exists(os.path.join(path, 'PGCTBBAT'))
            pgctbparam = os.path.exists(os.path.join(path, 'PGCTBPARAM'))
            pgctbsub = os.path.exists(os.path.join(path, 'PGCTBSUB'))
            pgctbsubws = os.path.exists(os.path.join(path, 'PGCTBSUBWS'))
            sqlca = os.path.exists(os.path.join(path, 'SQLCA'))
            if pgctbparam and pgctbsub and sqlca and pgctbsubws and pgctbbat:
                self.lineEditDbpreFramework.setText(path)
            else:
                QtWidgets.QMessageBox.warning(
                    self, tr('Invalid dpre framework directory'),
                    tr('Missing one of the following files: \n'
                    'PGCTBBAT: %s\n'
                    'PGCTBPARAM: %s\n'
                    'PGCTBSUB: %s\n'
                    'PGCTBSUBWS: %s\n'
                    'SQLCA: %s\n') % (
                        bool_to_string(pgctbbat), bool_to_string(pgctbparam),
                        bool_to_string(pgctbsub), bool_to_string(pgctbsubws),
                        bool_to_string(sqlca)))

    def stop_backend(self):
        self.plainTextEdit.backend.stop()
        self.plainTextEditPython.backend.stop()
        self.plainTextEditOther.backend.stop()

    def _update_icon_theme(self, c):
        index = self.comboBoxIconTheme.findText(c)
        self.comboBoxIconTheme.setCurrentIndex(index)

    def _on_show_pass_state_changed(self, state):
        if state:
            self.lineEditDBPASSWD.setEchoMode(QtWidgets.QLineEdit.Normal)
        else:
            self.lineEditDBPASSWD.setEchoMode(QtWidgets.QLineEdit.Password)

    @QtCore.Slot(bool)
    def on_radioButtonColorWhite_toggled(self, native):
        # choose an icon them that goes well with the selected style
        if not native:
            candidates = ['Faenza-Darkest', 'nouveGnomeGray', 'matefaenzadark',
                          'gnome', 'oxygen']
            available = system.icon_themes()
            for c in candidates:
                if c in available:
                    self._update_icon_theme(c)
                    break
        else:
            self._update_icon_theme('default')
        # choose a a color scheme that goes well with the selected style
        # (pour COBOL comme pour Python)
        target = 'qt' if native else 'darcula'
        self._color_schemes = {'cobol': target, 'python': target,
                               'other': target}
        self._select_color_scheme(target)

    def _setup_language_color_selector(self):
        """
        Ajoute au-dessus de la liste des thèmes un choix du langage (COBOL /
        Python), avec un aperçu propre à chaque langage : chaque langage a
        son thème de couleurs.
        """
        g = self.gridLayout_4
        g.removeWidget(self.listWidgetColorSchemes)
        g.removeWidget(self.plainTextEdit)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel(tr('Language:'), self.groupBox_7))
        self.comboColorLanguage = QtWidgets.QComboBox(self.groupBox_7)
        self.comboColorLanguage.addItem('COBOL', 'cobol')
        self.comboColorLanguage.addItem('Python / MicroPython', 'python')
        self.comboColorLanguage.addItem(tr('Other files'), 'other')
        bar.addWidget(self.comboColorLanguage)
        bar.addStretch()
        g.addLayout(bar, 0, 0, 1, 2)
        g.addWidget(self.listWidgetColorSchemes, 1, 0, 1, 1)
        g.addWidget(self.plainTextEdit, 1, 1, 1, 1)
        self.plainTextEditPython = PythonCodeEdit(self.groupBox_7)
        self.plainTextEditPython.setPlainText(
            PYTHON_TEMPLATE, 'text/x-python', 'utf-8')
        self.plainTextEditPython.hide()
        g.addWidget(self.plainTextEditPython, 1, 1, 1, 1)
        self.plainTextEditOther = GenericCodeEdit(self.groupBox_7)
        self.plainTextEditOther.setPlainText(
            OTHER_TEMPLATE, 'application/json', 'utf-8')
        # pas de nom de fichier : on indique le langage de l'exemple
        self.plainTextEditOther.syntax_highlighter.set_lexer_from_filename(
            'exemple.json')
        self.plainTextEditOther.syntax_highlighter.rehighlight()
        self.plainTextEditOther.hide()
        g.addWidget(self.plainTextEditOther, 1, 1, 1, 1)
        self._color_schemes = {}
        self._color_language = 'cobol'
        self.comboColorLanguage.currentIndexChanged.connect(
            self._on_color_language_changed)

    def _on_color_language_changed(self, index):
        self._color_language = self.comboColorLanguage.itemData(index)
        self.plainTextEdit.setVisible(self._color_language == 'cobol')
        self.plainTextEditPython.setVisible(self._color_language == 'python')
        self.plainTextEditOther.setVisible(self._color_language == 'other')
        self._select_color_scheme(
            self._color_schemes.get(self._color_language))

    def _select_color_scheme(self, name):
        for i in range(self.listWidgetColorSchemes.count()):
            if self.listWidgetColorSchemes.item(i).text() == name:
                self.listWidgetColorSchemes.setCurrentRow(i)
                break

    def update_color_scheme_preview(self, item):
        if item:
            self._color_schemes[self._color_language] = item.text()
            editor = {'python': self.plainTextEditPython,
                      'other': self.plainTextEditOther}.get(
                self._color_language, self.plainTextEdit)
            editor.syntax_highlighter.color_scheme = ColorScheme(item.text())

    def setupUi(self, Dialog):
        super().setupUi(Dialog)
        self.setMinimumWidth(450)
        self.tabWidget.setCurrentIndex(0)

    def _add_run_env_variable(self):
        index = self.tw_run_env.rowCount()
        self.tw_run_env.insertRow(index)
        focus_item = QtWidgets.QTableWidgetItem()
        self.tw_run_env.setItem(index, 0, focus_item)
        self.tw_run_env.setItem(index, 1, QtWidgets.QTableWidgetItem())
        self.tw_run_env.scrollToItem(focus_item)
        self.tw_run_env.editItem(focus_item)

    def _rm_run_env_variable(self):
        self.tw_run_env.removeRow(self.tw_run_env.currentRow())

    def _clear_run_env(self):
        self.tw_run_env.clearContents()
        self.tw_run_env.setRowCount(0)

    def reset(self, all_tabs=False):
        settings = Settings()
        # Editor
        if self.tabWidget.currentIndex() == 0 or all_tabs:
            self.cb_cursor_pos_in_bytes.setChecked(
                settings.show_cursor_pos_in_bytes)
            self.checkBoxShowErrors.setChecked(settings.show_errors)
            self.checkBoxShowHome.setChecked(settings.show_home_page)
            self.checkBoxViewLineNumber.setChecked(settings.display_lines)
            self.checkBoxHighlightCurrentLine.setChecked(
                settings.highlight_caret)
            self.checkBoxHighlightWhitespaces.setChecked(
                settings.show_whitespaces)
            self.spinBoxEditorTabLen.setValue(settings.tab_len)
            self.checkBoxEditorAutoIndent.setChecked(
                settings.enable_autoindent)
            self.spinBoxEditorCCTriggerLen.setValue(
                settings.code_completion_trigger_len)
            self.rbLowerCaseKwds.setChecked(settings.lower_case_keywords)
            self.rbUpperCaseKwds.setChecked(not settings.lower_case_keywords)
            self.lineEditCommentIndicator.setText(settings.comment_indicator)
            self.checkBoxSmartBackspace.setChecked(
                settings.enable_smart_backspace)
            self.checkBoxAutodetectEOL.setChecked(settings.autodetect_eol)
            self.comboBoxPreferredEOL.setCurrentIndex(settings.preferred_eol)
            self.comboCcFilterMode.setCurrentIndex(
                settings.completion_filter_mode)
            for pos, spin_box, color, picker in zip(
                    settings.margin_positions, self._margin_spin_boxes,
                    settings.margin_colors, self._margin_color_pickers):
                spin_box.setValue(pos + 1)
                picker.color = QtGui.QColor(color)
        # Style
        if self.tabWidget.currentIndex() == 1 or all_tabs:
            rb = (self.radioButtonColorDark if settings.dark_style else
                  self.radioButtonColorWhite)
            rb.setChecked(True)
            index = self.comboBoxIconTheme.findText(QtGui.QIcon.themeName())
            if index != -1:
                self.comboBoxIconTheme.setCurrentIndex(index)
            self.fontComboBox.setCurrentFont(QtGui.QFont(settings.font))
            self.spinBoxFontSize.setValue(settings.font_size)
            self.listWidgetColorSchemes.clear()
            for style in PYGMENTS_STYLES:
                self.listWidgetColorSchemes.addItem(style)
            self._color_schemes = {
                'cobol': settings.color_scheme,
                'python': settings.python_color_scheme,
                'other': settings.other_color_scheme}
            self._select_color_scheme(
                self._color_schemes[self._color_language])
        # Run
        if self.tabWidget.currentIndex() == 3 or all_tabs:
            self.checkBoxRunExtTerm.setChecked(settings.external_terminal)
            self.lineEditRunTerm.setVisible(sys.platform != 'win32')
            self.lbl_external_terminal_command.setVisible(
                sys.platform != 'win32')
            self.lineEditRunTerm.setEnabled(settings.external_terminal)
            self.lineEditRunTerm.setText(settings.external_terminal_command)
            self.tw_run_env.clearContents()
            self.tw_run_env.setRowCount(0)
            for key, value in Settings().run_environemnt.items():
                index = self.tw_run_env.rowCount()
                self.tw_run_env.insertRow(index)
                self.tw_run_env.setItem(
                    index, 0, QtWidgets.QTableWidgetItem(key))
                self.tw_run_env.setItem(
                    index, 1, QtWidgets.QTableWidgetItem(value))
            self.edit_working_dir.setText(settings.working_dir)
        # compiler
        if self.tabWidget.currentIndex() == 2 or all_tabs:
            self.cbAutoDetectSublmodules.setChecked(
                Settings().autodetect_submodules)
            self.cb_copy_runtime_dlls.setVisible(sys.platform == 'win32')
            self.cb_copy_runtime_dlls.setChecked(Settings().copy_runtime_dlls)
            self.lineEditOutputDirectory.setText(Settings().output_directory)
            self.lineEditCobcExts.setText(';'.join(Settings().cobc_extensions))
            self.checkBoxFreeFormat.setChecked(settings.free_format)
            self.comboBoxStandard.setCurrentIndex(
                int(settings.cobol_standard))
            self.lineEditCompilerPath.setText(settings.compiler_path)
            flags = Settings().compiler_flags
            self.cb_debugging_line.setChecked(
                self.cb_debugging_line.text() in flags)
            self.cb_ftrace.setChecked(self.cb_ftrace.text().replace('&', '') in flags)
            self.cb_ftraceall.setChecked(self.cb_ftraceall.text().replace('&', '') in flags)
            self.cb_g.setChecked(self.cb_g.text().replace('&', '') in flags)
            self.cb_static.setChecked(self.cb_static.text().replace('&', '') in flags)
            self.cb_debug.setChecked(self.cb_debug.text().replace('&', '') in flags)
            self.cb_w.setChecked(self.cb_w.text().replace('&', '') in flags)
            self.cb_wall.setChecked(self.cb_wall.text().replace('&', '') in flags)
            for v in self.flags_in_checkbox:
                try:
                    flags.remove(v)
                except ValueError:
                    pass
            self.lineEditLibs.setText(settings.libraries)
            self.listWidgetLibPaths.addItems(
                [pth for pth in settings.library_search_path.split(';')
                 if pth])
            self.listWidgetCopyPaths.addItems(
                [pth for pth in settings.copybook_paths.split(';')
                 if pth])
            self.le_compiler_flags.setText(' '.join(flags))
            if system.windows:
                self.lineEditVCVARS.setText(settings.vcvarsall)
                self.combo_arch.setCurrentIndex(
                    1 if settings.vcvarsall_arch == 'x64' else 0)
            self.PATH.setText(settings.path)
            self.cbPATH.setChecked(settings.path_enabled)
            self.COB_CONFIG_DIR.setText(settings.cob_config_dir)
            self.cbCOB_CONFIG_DIR.setChecked(settings.cob_config_dir_enabled)
            self.COB_COPY_DIR.setText(settings.cob_copy_dir)
            self.cbCOB_COPY_DIR.setChecked(settings.cob_copy_dir_enabled)
            self.COB_INCLUDE_PATH.setText(settings.cob_include_path)
            self.cbCOB_INCLUDE_PATH.setChecked(settings.cob_include_path_enabled)
            self.COB_LIB_PATH.setText(settings.cob_lib_path)
            self.cbCOB_LIB_PATH.setChecked(settings.cob_lib_path_enabled)

        # SQL Cobol
        if self.tabWidget.currentIndex() == 4 or all_tabs:
            self.lineEditDbpreExts.setText(';'.join(Settings().dbpre_extensions))
            self.lineEditDbpre.setText(settings.dbpre)
            self.lineEditDbpreFramework.setText(settings.dbpre_framework)
            self.lineEditCobmysqlapi.setText(settings.cobmysqlapi)
            self.lineEditDBHOST.setText(settings.dbhost)
            self.lineEditDBUSER.setText(settings.dbuser)
            self.lineEditDBPASSWD.setText(settings.dbpasswd)
            self.lineEditDBNAME.setText(settings.dbname)
            self.lineEditDBPORT.setText(settings.dbport)
            self.lineEditDBSOCKET.setText(settings.dbsocket)
            self.labelDbpreVersion.setText(
                compilers.DbpreCompiler().get_version()
                if Settings().dbpre != '' else '')
            self.lineEditESQLOC.setText(settings.esqloc)
            self.lineEditesqlOcExts.setText(';'.join(Settings().esqloc_extensions))

    def restore_defaults(self):
        settings = Settings()
        index = self.tabWidget.currentIndex()
        # Editor
        if index == 0:
            settings.show_cursor_pos_in_bytes = False
            settings.show_error = True
            settings.display_lines = True
            settings.highlight_caret = True
            settings.show_whitespaces = False
            settings.tab_len = 4
            settings.enable_autoindent = True
            settings.code_completion_trigger_len = 1
            settings.comment_indicator = '*> '
            settings.enable_smart_backspace = False
            settings.lower_case_keywords = False
            settings.autodetect_eol = True
            settings.preferred_eol = 0
            settings.code_completion_trigger_len = 1
            settings.margin_positions = [7, 11, 72, 79]
            settings.margin_colors = ['red'] * 4
        # Style
        elif index == 1:
            settings.dark_style = False
            settings.icon_theme = ''
            settings.font = 'Source Code Pro'
            settings.font_size = 11
            settings.colorScheme = 'qt'
        # run
        elif index == 3:
            settings.external_terminal = False
            settings.external_terminal_command = None
            settings.run_environemnt = {}
            settings.working_dir = ''
        # compiler
        elif index == 2:
            settings.autodetect_submodules = True
            settings.output_directory = 'bin'
            settings.copy_runtime_dlls = False
            settings.free_format = False
            settings.cobol_standard = GnuCobolStandard.default
            settings.compiler_path = 'cobc.exe' if system.windows else 'cobc'
            settings.compiler_flags = ['-debug', '-Wall']
            settings.copybook_paths = ''
            settings.library_search_path = ''
            settings.libraries = ''
            settings.cobc_extensions = ['.cob', '.cbl', '.pco', '.lst']
            if system.windows:
                settings.vcvarsall = ''
                settings.vcvarsall_arch = 'x86'
            settings.path = settings.default_path()
            settings.path_enabled = True
            settings.cob_config_dir = settings.default_config_dir()
            settings.cob_config_dir_enabled = system.windows
            settings.cob_copy_dir = settings.default_copy_dir()
            settings.cob_copy_dir_enabled = system.windows
            settings.cob_include_path = settings.default_include_dir()
            settings.cob_include_path_enabled = system.windows
            settings.cob_lib_path = settings.default_lib_path()
            settings.cob_lib_path_enabled = system.windows
        elif index == 4:
            settings.dbpre = ''
            settings.dbpre_extensions = ['.scb']
            settings.dbpre_framework = ''
            settings.cobmysqlapi = ''
            settings.dbhost = 'localhost'
            settings.dbuser = ''
            settings.dbpasswd = ''
            settings.dbname = ''
            settings.dbport = '03306'
            settings.dbsocket = 'null'
            settings.esqloc = ''
            settings.esqloc_extensions = ['.sqb']
        self.reset()

    def apply(self):
        # force next check (otherwise the cached result will be used)
        compilers.GnuCobolCompiler.check_compiler.reset()
        settings = Settings()
        settings.show_cursor_pos_in_bytes = \
            self.cb_cursor_pos_in_bytes.isChecked()
        settings.display_lines = self.checkBoxViewLineNumber.isChecked()
        settings.highlight_caret = self.checkBoxHighlightCurrentLine.isChecked()
        settings.show_whitespaces = \
            self.checkBoxHighlightWhitespaces.isChecked()
        settings.tab_len = self.spinBoxEditorTabLen.value()
        settings.enable_autoindent = self.checkBoxEditorAutoIndent.isChecked()
        settings.code_completion_trigger_len = \
            self.spinBoxEditorCCTriggerLen.value()
        settings.preferred_eol = self.comboBoxPreferredEOL.currentIndex()
        settings.autodetect_eol = self.checkBoxAutodetectEOL.isChecked()
        settings.dark_style = self.radioButtonColorDark.isChecked()
        settings.font = self.fontComboBox.currentFont().family()
        settings.font_size = self.spinBoxFontSize.value()
        settings.color_scheme = self._color_schemes.get(
            'cobol', settings.color_scheme)
        settings.python_color_scheme = self._color_schemes.get(
            'python', settings.python_color_scheme)
        settings.other_color_scheme = self._color_schemes.get(
            'other', settings.other_color_scheme)
        settings.external_terminal = self.checkBoxRunExtTerm.isChecked()
        settings.external_terminal_command = self.lineEditRunTerm.text()
        settings.lower_case_keywords = self.rbLowerCaseKwds.isChecked()
        settings.compiler_path = self.lineEditCompilerPath.text()
        settings.vcvarsall = self.lineEditVCVARS.text()
        settings.vcvarsall_arch = self.combo_arch.currentText()
        settings.path = self.PATH.text()
        settings.path_enabled = self.cbPATH.isChecked()
        settings.cob_config_dir = self.COB_CONFIG_DIR.text()
        settings.cob_config_dir_enabled = self.cbCOB_CONFIG_DIR.isChecked()
        settings.cob_copy_dir = self.COB_COPY_DIR.text()
        settings.cob_copy_dir_enabled = self.cbCOB_COPY_DIR.isChecked()
        settings.cob_include_path = self.COB_INCLUDE_PATH.text()
        settings.cob_include_path_enabled = self.cbCOB_INCLUDE_PATH.isChecked()
        settings.cob_lib_path = self.COB_LIB_PATH.text()
        settings.cob_lib_path_enabled = self.cbCOB_LIB_PATH.isChecked()
        settings.free_format = self.checkBoxFreeFormat.isChecked()
        settings.comment_indicator = self.lineEditCommentIndicator.text()
        settings.autodetect_submodules = \
            self.cbAutoDetectSublmodules.isChecked()
        settings.cobol_standard = GnuCobolStandard(
            self.comboBoxStandard.currentIndex())
        settings.icon_theme = self.comboBoxIconTheme.currentText()
        settings.show_errors = self.checkBoxShowErrors.isChecked()
        settings.show_home_page = self.checkBoxShowHome.isChecked()
        settings.enable_smart_backspace = \
            self.checkBoxSmartBackspace.isChecked()
        paths = []
        for i in range(self.listWidgetLibPaths.count()):
            paths.append(self.listWidgetLibPaths.item(i).text())
        settings.library_search_path = ';'.join(paths)
        paths = []
        for i in range(self.listWidgetCopyPaths.count()):
            paths.append(self.listWidgetCopyPaths.item(i).text())
        settings.copybook_paths = ';'.join(paths)
        settings.libraries = self.lineEditLibs.text()
        settings.output_directory = self.lineEditOutputDirectory.text()
        settings.copy_runtime_dlls = self.cb_copy_runtime_dlls.isChecked()
        cb_flags = [self.cb_g, self.cb_ftrace, self.cb_ftraceall,
                    self.cb_debugging_line, self.cb_static, self.cb_debug,
                    self.cb_w, self.cb_wall]
        flags = [cb.text() for cb in cb_flags if cb.isChecked()]
        flags += system.shell_split(self.le_compiler_flags.text())
        settings.compiler_flags = flags
        # sql
        settings.dbpre = self.lineEditDbpre.text()
        settings.dbpre_framework = self.lineEditDbpreFramework.text()
        settings.cobmysqlapi = self.lineEditCobmysqlapi.text()
        settings.dbhost = self.lineEditDBHOST.text()
        settings.dbuser = self.lineEditDBUSER.text()
        settings.dbpasswd = self.lineEditDBPASSWD.text()
        settings.dbname = self.lineEditDBNAME.text()
        settings.dbport = self.lineEditDBPORT.text()
        settings.dbsocket = self.lineEditDBSOCKET.text()
        settings.esqloc = self.lineEditESQLOC.text()
        settings.completion_filter_mode = \
            self.comboCcFilterMode.currentIndex()
        settings.cobc_extensions = [
            ext.lower() for ext in
            self.lineEditCobcExts.text().split(';') if ext]
        settings.dbpre_extensions = [
            ext.lower() for ext in
            self.lineEditDbpreExts.text().split(';') if ext]
        settings.esqloc_extensions = [
            ext for ext in self.lineEditesqlOcExts.text().split(';') if ext]

        colors = []
        positions = []
        for spin_box, picker in zip(self._margin_spin_boxes,
                                    self._margin_color_pickers):
            positions.append(spin_box.value() - 1)
            colors.append(picker.color.name())
        settings.margin_positions = positions
        settings.margin_colors = colors

        env = {}
        for i in range(self.tw_run_env.rowCount()):
            env[self.tw_run_env.item(i, 0).text()] = self.tw_run_env.item(
                i, 1).text()
        settings.run_environemnt = env
        settings.working_dir = self.edit_working_dir.text()

    @classmethod
    def edit_preferences(cls, parent):
        def restore_state(dlg):
            s = Settings()
            dlg.resize(s.preferences_width, s.preferences_height)
            dlg.tabWidget.setCurrentIndex(s.preferences_index)

        def save_state(dlg):
            s = Settings()
            s.preferences_width = dlg.width()
            s.preferences_height = dlg.height()
            s.preferences_index = dlg.tabWidget.currentIndex()

        dlg = cls(parent)
        restore_state(dlg)
        ret_val = dlg.exec_()
        dlg.stop_backend()
        if ret_val != dlg.Accepted:
            Settings().import_from_dict(dlg.initial_settings)
            raise ValueError()
        save_state(dlg)
        dlg.apply()
