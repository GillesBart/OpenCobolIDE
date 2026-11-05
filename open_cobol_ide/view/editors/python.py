"""
Éditeur de code Python / MicroPython.

Il s'appuie sur l'éditeur générique de pyqode (coloration Pygments, repliage
par indentation, complétion sur les mots du document) et ajoute :
    - une auto-indentation adaptée à Python (après ':' et les parenthèses
      ouvertes, retrait après return/pass/break/continue/raise),
    - le commentaire / décommentaire de lignes avec Ctrl+/,
    - le plan du fichier (classes, fonctions, méthodes, variables),
    - la vérification de la syntaxe pendant la frappe (marge et soulignement).
"""
import re
import tokenize

from pyqode.core import modes, panels
from pyqode.core.api import Mode, Panel
from pyqode.core.modes import AutoIndentMode
from pyqode.qt import QtGui, QtWidgets, QtCore

from open_cobol_ide.python_checker import check_python
from open_cobol_ide.python_outline import get_python_outline
from open_cobol_ide.translation import tr
from .generic import GenericCodeEdit

_DEDENT_RE = re.compile(r'(return|pass|break|continue|raise)\b')


def python_source_encoding(path):
    """
    Encodage d'un fichier source Python : BOM ou cookie ``# coding:``, sinon
    UTF-8 (comme l'interpréteur), et non l'encodage de la machine (cp1252).

    :return: le nom de l'encodage, ou None si le fichier est illisible avec cet
        encodage (ancien fichier ANSI) : l'appelant garde alors son
        comportement habituel (encodage mémorisé ou panneau de choix).
    """
    try:
        with open(path, 'rb') as f:
            encoding, _ = tokenize.detect_encoding(f.readline)
            f.seek(0)
            f.read().decode(encoding)
    except (OSError, SyntaxError, LookupError, UnicodeError):
        return None
    return encoding


def scan_line(line):
    """
    Analyse une ligne de code Python.

    :return: (code sans commentaire, profondeur de parenthèses, guillemet
             encore ouvert en fin de ligne ou None)
    """
    quote = None
    depth = 0
    out = []
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == '\\':
                out.append(line[i:i + 2])
                i += 2
                continue
            if line.startswith(quote, i):
                out.append(quote)
                i += len(quote)
                quote = None
                continue
            out.append(c)
        else:
            if c == '#':
                break
            if c in '"\'':
                quote = c * 3 if line.startswith(c * 3, i) else c
                out.append(quote)
                i += len(quote)
                continue
            if c in '([{':
                depth += 1
            elif c in ')]}':
                depth -= 1
            out.append(c)
        i += 1
    return ''.join(out), depth, quote


class PythonAutoIndentMode(AutoIndentMode):
    """
    Indentation automatique à l'appui sur Entrée : reprend l'indentation de la
    ligne courante, avec un niveau de plus après ':' ou une parenthèse
    ouverte, et un niveau de moins après return/pass/break/continue/raise.
    """

    def _get_indent(self, cursor):
        pre, post = super()._get_indent(cursor)
        line = cursor.block().text()[:cursor.positionInBlock()]
        code, depth, quote = scan_line(line)
        if quote is not None:
            return pre, post   # on est dans une chaîne
        code = code.rstrip()
        unit = ' ' * self.editor.tab_length
        if depth > 0 or code.endswith((':', '\\')):
            post += unit
        elif _DEDENT_RE.match(code.lstrip()) and len(post) >= len(unit):
            post = post[:-len(unit)]
        return pre, post


class HashCommentsMode(Mode):
    """
    Commente / décommente les lignes sélectionnées avec Ctrl+/ (la ligne
    courante si rien n'est sélectionné, puis le curseur passe à la suivante).
    """

    def on_state_changed(self, state):
        if state:
            self.action = QtWidgets.QAction(tr('Comment/Uncomment'),
                                            self.editor)
            self.action.setShortcut('Ctrl+/')
            self.action.triggered.connect(self.comment)
            self.editor.add_action(self.action, sub_menu=None)
            self.editor.key_pressed.connect(self._on_key_pressed)
        else:
            self.editor.remove_action(self.action, sub_menu=None)
            self.action.triggered.disconnect(self.comment)
            self.editor.key_pressed.disconnect(self._on_key_pressed)

    def _on_key_pressed(self, event):
        # le « / » du pavé numérique n'active pas toujours le raccourci
        ctrl = event.modifiers() & QtCore.Qt.ControlModifier
        if ctrl and event.key() == QtCore.Qt.Key_Slash:
            self.comment()
            event.accept()

    def _selected_blocks(self):
        doc = self.editor.document()
        cursor = self.editor.textCursor()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(cursor.selectionEnd())
        if (cursor.hasSelection() and last.blockNumber() > first.blockNumber()
                and cursor.selectionEnd() == last.position()):
            last = last.previous()   # sélection finissant en début de ligne
        blocks = []
        block = first
        while block.isValid():
            blocks.append(block)
            if block.blockNumber() >= last.blockNumber():
                break
            block = block.next()
        return blocks

    def comment(self):
        has_selection = self.editor.textCursor().hasSelection()
        blocks = self._selected_blocks()
        texts = [b.text() for b in blocks if b.text().strip()]
        if not texts:
            return
        uncomment = all(t.lstrip().startswith('#') for t in texts)
        indent = min(len(t) - len(t.lstrip()) for t in texts)
        edit = QtGui.QTextCursor(self.editor.document())
        edit.beginEditBlock()
        for block in blocks:
            text = block.text()
            if not text.strip():
                continue
            c = QtGui.QTextCursor(block)
            if uncomment:
                pos = len(text) - len(text.lstrip())
                c.setPosition(block.position() + pos)
                size = 2 if text[pos:pos + 2] == '# ' else 1
                c.movePosition(c.Right, c.KeepAnchor, size)
                c.removeSelectedText()
            else:
                c.setPosition(block.position() + indent)
                c.insertText('# ')
        edit.endEditBlock()
        if not has_selection:
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.Down, cursor.MoveAnchor)
            self.editor.setTextCursor(cursor)


class PythonCodeEdit(GenericCodeEdit):
    """
    Éditeur Python / MicroPython.
    """
    mimetypes = ['text/x-python']
    extensions = ['.py', '.pyw', '.pyi']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.modes.remove('AutoIndentMode')
        self.auto_indent_mode = self.modes.append(PythonAutoIndentMode())
        self.comments_mode = self.modes.append(HashCommentsMode())
        self.outline_mode = self.modes.append(
            modes.OutlineMode(get_python_outline))
        self.linter_mode = self.modes.append(modes.CheckerMode(check_python))
        self.panels.append(panels.CheckerPanel(), Panel.Position.LEFT)
        self.panels.append(panels.GlobalCheckerPanel(), Panel.Position.RIGHT)
        self.app = None

    def apply_ide_settings(self, settings):
        """
        Applique les préférences de l'IDE qui concernent cet éditeur (les
        autres, propres à COBOL, sont ignorées).
        """
        self.auto_indent_mode.enabled = settings.enable_autoindent
        self.linter_mode.enabled = settings.show_errors
        self.modes.get('CaretLineHighlighterMode').enabled = \
            settings.highlight_caret
        line_nbr = self.panels.get('LineNumberPanel')
        line_nbr.enabled = settings.display_lines
        line_nbr.setVisible(settings.display_lines)
        completion = self.modes.get('CodeCompletionMode')
        completion.trigger_length = settings.code_completion_trigger_len
        completion.smart_completion = bool(settings.completion_filter_mode)
        self.file.autodetect_eol = settings.autodetect_eol
        self.file.preferred_eol = settings.preferred_eol

    def close(self, clear=True):
        self.auto_indent_mode = None
        self.comments_mode = None
        self.outline_mode = None
        self.linter_mode = None
        super().close(clear=clear)
        self.app = None

    def clone(self):
        clone = self.__class__(parent=self.parent())
        clone.app = self.app
        return clone
