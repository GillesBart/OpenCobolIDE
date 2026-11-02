"""
Interface en français : traducteur Qt qui lit translations/fr.json.
"""
import json
import logging
import os

from pyqode.qt import QtCore

from open_cobol_ide import system


def _logger():
    return logging.getLogger(__name__)


class JsonTranslator(QtCore.QTranslator):
    def __init__(self, table, replace, parent=None):
        super().__init__(parent)
        self._table = table
        self._replace = replace

    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        tr = self._table.get(source)
        if tr:
            return tr
        if '<' in source:  # gros textes HTML : remplacement par morceaux
            tr = source
            for en, fr in self._replace:
                tr = tr.replace(en, fr)
            if tr != source:
                return tr
        return None  # non traduit : Qt garde le texte anglais


def _find():
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (system.app_dir(), here):
        path = os.path.join(base, 'translations', 'fr.json')
        if os.path.isfile(path):
            return path
    return None


def install(app):
    """
    Installe la traduction française (OCIDE_LANG=en pour la désactiver).
    Ne doit jamais empêcher le démarrage.
    """
    if os.environ.get('OCIDE_LANG', '').lower() == 'en':
        return
    try:
        path = _find()
        if path is None:
            _logger().warning('translations/fr.json not found')
            return
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        replace = data.pop('__replace__', [])
        tr = JsonTranslator(data, replace, app)
        app.installTranslator(tr)
        app._ocide_translator = tr  # garder une référence
        _logger().info('french translation loaded from %s', path)
    except Exception:
        _logger().exception('failed to load the french translation')