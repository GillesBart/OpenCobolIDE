#!/usr/bin/env python3
"""
Outil de gestion des traductions.

    python scripts/translations.py update fr      # crée/met à jour fr.json
    python scripts/translations.py update de      # crée une nouvelle langue
    python scripts/translations.py check fr       # liste les textes non traduits

'update' recense tous les textes à traduire dans le code (fichiers *_ui.py,
appels _('...') de pyqode et tr('...') de l'IDE), ajoute les nouveaux avec une
valeur vide, conserve les traductions existantes et signale les textes qui ne
sont plus utilisés. Il ne supprime jamais rien : les textes inutilisés sont
simplement listés.

Pour utiliser une langue : set OCIDE_LANG=<code> avant de lancer l'IDE.
"""
import ast
import glob
import json
import os
import sys
import warnings

warnings.simplefilter('ignore')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'open_cobol_ide')
TR_DIR = os.path.join(SRC, 'translations')


def _name(func):
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)


def collect():
    """Renvoie la liste ordonnée des textes à traduire."""
    found = {}
    pattern = os.path.join(SRC, '**', '*.py')
    for path in sorted(glob.glob(pattern, recursive=True)):
        norm = path.replace('\\', '/')
        if '/pyqode/qt/' in norm or '/translation.py' in norm:
            continue
        if '/extlibs/' in norm and '/extlibs/pyqode/' not in norm:
            continue  # bibliothèques tierces
        try:
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        consts = {}  # constantes de module : tr(UNC_WARNING)
        for stmt in tree.body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                consts[stmt.targets[0].id] = stmt.value
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = _name(node.func)
            if func == '_translate' and len(node.args) >= 2:
                arg = node.args[1]
            elif func in ('_', 'tr') and node.args:
                arg = node.args[0]
            else:
                continue
            if isinstance(arg, ast.Name) and arg.id in consts:
                arg = consts[arg.id]
            if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and arg.value.strip()):
                if arg.value.startswith('<!DOCTYPE'):
                    continue  # gros textes HTML : voir "replace"
                found.setdefault(arg.value, None)
    return list(found)


def _path(lang):
    return os.path.join(TR_DIR, lang + '.json')


def _load(lang):
    path = _path(lang)
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {'language': lang, 'name': lang, 'replace': [], 'strings': {}}


def update(lang):
    keys = collect()
    data = _load(lang)
    old = data.get('strings', {})
    new = {k: old.get(k, '') for k in keys}
    unused = [k for k in old if k not in new]
    for k in unused:           # on garde les anciens textes, à la fin
        new[k] = old[k]
    data['strings'] = new
    os.makedirs(TR_DIR, exist_ok=True)
    with open(_path(lang), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    todo = [k for k in keys if not new[k]]
    print('%s : %d textes, %d à traduire, %d inutilisés conservés'
          % (_path(lang), len(keys), len(todo), len(unused)))
    return todo


def check(lang):
    keys = collect()
    strings = _load(lang).get('strings', {})
    todo = [k for k in keys if not strings.get(k)]
    for k in todo:
        print(repr(k))
    print('%d textes sans traduction sur %d' % (len(todo), len(keys)))


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('update', 'check'):
        print(__doc__)
        sys.exit(1)
    {'update': update, 'check': check}[sys.argv[1]](sys.argv[2])
