"""
Traduction de l'interface.

Un seul fichier par langue : translations/<langue>.json

    {
      "language": "fr",
      "name": "Français",
      "replace": [["morceau de texte HTML anglais", "morceau traduit"], ...],
      "strings": {"English text": "Texte traduit", ...}
    }

* "strings" : texte anglais -> texte traduit (une valeur vide = non traduit).
  La même table sert aux textes Qt (fichiers *_ui.py), aux textes gettext
  de pyqode (_()) et aux textes du code (tr()).
* "replace" : remplacements par morceaux pour les gros textes HTML
  (fenêtre « À propos »), sans avoir à recopier tout le texte.

La langue est choisie avec la variable d'environnement OCIDE_LANG
(défaut : fr ; OCIDE_LANG=en pour garder l'anglais).
Pour ajouter une langue : python scripts/translations.py update <code>
"""
import gettext
import json
import logging
import os

from pyqode.qt import QtCore

from open_cobol_ide import system

DEFAULT_LANGUAGE = 'fr'

_TABLE = {}      # texte anglais -> texte traduit
_REPLACE = []    # [(anglais, traduit), ...] pour les textes HTML
_language = 'en'


def _logger():
    return logging.getLogger(__name__)


def tr(text):
    """
    Traduit un texte de l'interface. Renvoie le texte tel quel s'il n'a pas
    de traduction. À appeler sur le texte *avant* son formatage : tr('%s') % x
    """
    return _TABLE.get(text) or text


def current_language():
    return _language


def translations_dir():
    for base in (system.app_dir(), os.path.dirname(os.path.abspath(__file__))):
        path = os.path.join(base, 'translations')
        if os.path.isdir(path):
            return path
    return None


class JsonTranslator(QtCore.QTranslator):
    """Traducteur Qt (textes des fichiers *_ui.py)."""

    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        text = _TABLE.get(source)
        if text:
            return text
        if '<' in source:  # gros textes HTML : remplacement par morceaux
            text = source
            for en, fr in _REPLACE:
                text = text.replace(en, fr)
            if text != source:
                return text
        return None  # non traduit : Qt garde le texte anglais


class JsonGettext(gettext.NullTranslations):
    """Traducteur gettext (_() de pyqode)."""

    def gettext(self, message):
        return _TABLE.get(message) or message

    def ngettext(self, singular, plural, n):
        return self.gettext(singular if n == 1 else plural)


def load(language):
    """
    Charge translations/<language>.json dans la table. Renvoie True si OK.
    """
    global _language
    folder = translations_dir()
    if folder is None:
        _logger().warning('translations folder not found')
        return False
    candidates = [language]
    if '_' in language:
        candidates.append(language.split('_')[0])
    for code in candidates:
        path = os.path.join(folder, code + '.json')
        if not os.path.isfile(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _TABLE.clear()
        _TABLE.update({k: v for k, v in data.get('strings', {}).items() if v})
        _REPLACE[:] = [tuple(p) for p in data.get('replace', [])]
        _language = code
        _logger().info('translation %r loaded from %s', code, path)
        return True
    _logger().warning('no translation file for language %r in %s',
                      language, folder)
    return False


def _install_qt_translations(app, code):
    """
    Boutons standard de Qt (Oui/Non/Annuler...) : qtbase_<langue>.qm, fourni
    par PyQt5 ou copié dans translations/ lors du gel.
    """
    translator = QtCore.QTranslator(app)
    folders = [QtCore.QLibraryInfo.location(
        QtCore.QLibraryInfo.TranslationsPath), translations_dir()]
    for folder in folders:
        if folder and translator.load('qtbase_' + code, folder):
            app.installTranslator(translator)
            app._ocide_qt_translator = translator
            return True
    return False


def install(app):
    """
    Installe la traduction (OCIDE_LANG=en pour désactiver). Ne doit jamais
    empêcher le démarrage de l'application.
    """
    language = os.environ.get('OCIDE_LANG', '').strip() or DEFAULT_LANGUAGE
    if language.lower() in ('en', 'c', 'en_us', 'en_gb'):
        return
    try:
        if not load(language):
            return
        translator = JsonTranslator(app)
        app.installTranslator(translator)
        app._ocide_translator = translator  # garder une référence
        JsonGettext().install()  # _() de pyqode
        _install_qt_translations(app, _language)
    except Exception:
        _logger().exception('failed to load the translation')
