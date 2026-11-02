"""
Plan (outline) d'un fichier Python : classes, fonctions, méthodes et variables
de premier niveau.

Cette fonction est exécutée dans le processus backend (voir
pyqode.core.modes.OutlineMode) : elle ne doit donc dépendre ni de Qt ni de
l'interface.
"""
import ast

from pyqode.core.share import Definition

# icônes fournies par les ressources de pyqode.cobol (thème, ressource Qt)
ICON_CLASS = ['code-class', ':/icons/share/icons/pyqode.cobol/class.png']
ICON_FUNC = ['code-function', ':/icons/share/icons/pyqode.cobol/func.png']
ICON_VAR = ['code-variable', ':/icons/share/icons/pyqode.cobol/var.png']


def _short(text, size=60):
    text = ' '.join(text.split())
    return text if len(text) <= size else text[:size - 1] + '…'


def _signature(node):
    prefix = 'async def ' if isinstance(node, ast.AsyncFunctionDef) else 'def '
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = '...'
    return '%s%s(%s)' % (prefix, node.name, args)


def _variables(node):
    """Noms simples affectés par une instruction (a = 1, a: int = 1)."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return
    value = getattr(node, 'value', None)
    for target in targets:
        if isinstance(target, ast.Name):
            yield target.id, value


def _walk(body, depth=0):
    for node in body:
        if isinstance(node, ast.ClassDef):
            bases = ', '.join(_short(ast.unparse(b)) for b in node.bases)
            definition = Definition(
                node.name, node.lineno - 1, node.col_offset, ICON_CLASS,
                'class %s(%s)' % (node.name, bases) if bases
                else 'class %s' % node.name)
            definition.children = list(_walk(node.body, depth + 1))
            yield definition
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # les fonctions imbriquées dans une fonction ne sont pas listées
            yield Definition(node.name, node.lineno - 1, node.col_offset,
                             ICON_FUNC, _signature(node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name, value in _variables(node):
                if name.startswith('__') and name.endswith('__'):
                    continue
                yield Definition(
                    name, node.lineno - 1, node.col_offset, ICON_VAR,
                    _short('%s = %s' % (name, ast.unparse(value)))
                    if value is not None else name)
        elif isinstance(node, ast.If):
            # if __name__ == '__main__': ... / code spécifique à une carte
            yield from _walk(node.body, depth)
            yield from _walk(node.orelse, depth)
        elif isinstance(node, ast.Try):
            yield from _walk(node.body, depth)
            for handler in node.handlers:
                yield from _walk(handler.body, depth)
            yield from _walk(node.orelse, depth)
            yield from _walk(node.finalbody, depth)
        elif isinstance(node, ast.With):
            yield from _walk(node.body, depth)


def get_python_outline(data):
    """
    Worker du mode d'outline : renvoie la liste des définitions de premier
    niveau, ou None si le code est (momentanément) invalide, auquel cas le
    plan précédent est conservé.
    """
    try:
        tree = ast.parse(data['code'])
    except (SyntaxError, ValueError, RecursionError):
        return None
    return [d.to_dict() for d in _walk(tree.body)]
