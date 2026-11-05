"""
Vérification de la syntaxe d'un fichier Python pendant la frappe.

Cette fonction est exécutée dans le processus backend (voir
pyqode.core.modes.CheckerMode) : elle ne doit donc dépendre ni de Qt ni de
l'interface. Le code n'est jamais exécuté, seulement compilé : les erreurs de
syntaxe (et quelques erreurs détectées à la compilation, comme un « return »
hors d'une fonction) sont signalées, ainsi que les SyntaxWarning (séquence
d'échappement invalide...).
"""
import warnings

ERROR = 2
WARNING = 1


def _message(description, status, line, col):
    # CheckerMessage : lignes numérotées à partir de 0
    return description, status, max(line - 1, 0), col


def check_python(request_data):
    """
    :param request_data: dict avec 'code' (str) et 'path'
    :return: liste de (description, statut, ligne, colonne)
    """
    code = request_data['code']
    messages = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            compile(code, request_data.get('path') or '<editeur>', 'exec',
                    dont_inherit=True)
        except SyntaxError as e:
            col = e.offset - 1 if e.offset else None
            messages.append(_message(
                '%s: %s' % (type(e).__name__, e.msg), ERROR,
                e.lineno or 1, col))
        except (ValueError, RecursionError, MemoryError) as e:
            # caractère nul, expression trop imbriquée...
            messages.append(_message(str(e), ERROR, 1, None))
    for w in caught:
        if issubclass(w.category, SyntaxWarning):
            messages.append(_message(
                'SyntaxWarning: %s' % w.message, WARNING, w.lineno or 1, None))
    return messages
