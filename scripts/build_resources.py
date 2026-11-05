"""
Régénère open_cobol_ide/view/forms/ide_rc.py à partir de forms/ide.qrc.

À lancer (venv activé) après avoir ajouté ou remplacé une icône dans
forms/rc/ :   python scripts/build_resources.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMS = os.path.join(ROOT, 'forms')
OUT = os.path.join(ROOT, 'open_cobol_ide', 'view', 'forms', 'ide_rc.py')

subprocess.check_call(
    [sys.executable, '-m', 'PyQt5.pyrcc_main', 'ide.qrc', '-o', OUT],
    cwd=FORMS)
with open(OUT, encoding='utf-8', newline='') as f:
    text = f.read()
# le projet utilise pyqode.qt comme couche d'abstraction
assert 'from PyQt5 import QtCore' in text
text = text.replace('from PyQt5 import QtCore', 'from pyqode.qt import QtCore')
with open(OUT, 'w', encoding='utf-8', newline='') as f:
    f.write(text)
print('ide_rc.py régénéré :', OUT)
