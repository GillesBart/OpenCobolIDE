#!/usr/bin/env python3
"""
This scripts freeze the application and try to run the inno setup compiler
to create an setup executable.
"""
import glob
import os
os.chdir('..')
import shutil
import sys
from cx_Freeze import setup, Executable
from pyqode.core.tools import console
from open_cobol_ide import __version__, backend
from pyqode.core.backend import server as core_server
from pyqode.core.api.syntax_highlighter import get_all_styles
from pygments import lexers

version = __version__
if '.dev' in version or '.a' in version or '.b' in version or '.rc' in version:
    version = version[:version.index('.dev')]

# Detect system
windows = sys.platform == 'win32'
osx = sys.platform == 'darwin'

app_script = 'OpenCobolIDE.pyw'
app_name = 'OpenCobolIDE'
app_exe = 'OpenCobolIDE.exe' if windows else 'OpenCobolIDE'

# cobol backend
srv_script = backend.__file__
srv_exe = 'cobol-backend.exe' if windows else 'cobol-backend'

# core backend (for non cobol files completion).
core_srv_script = core_server.__file__
core_srv_exe = 'core-backend.exe' if windows else 'core-backend'

# pyqode console (for run in terminal)
console_script = console.__file__
console_name = 'pyqode-console'
console_exe = 'pyqode-console.exe' if windows else 'pyqode-console'


app_icon = 'forms/rc/silex-icon.ico' if windows else 'share/silex-icon.icns'


if len(sys.argv) == 1:
    sys.argv.append('build')


# collect pygments styles
pygments_styles = []
for s in get_all_styles():
    module = 'pygments.styles.%s' % s.replace('-', '_')
    try:
        __import__(module)
    except ImportError:
        pass
    else:
        pygments_styles.append(module)
print('pygments styles:', len(pygments_styles))

pygments_lexers = []
lexers_dir = os.path.dirname(lexers.__file__)
for file in os.listdir(lexers_dir):
    name = os.path.splitext(file)[0]
    if name not in ['__pycache__']:
        pygments_lexers.append('pygments.lexers.%s' % name)
print('pygments lexers:', len(pygments_lexers))


# build options
options = {
    'include_msvcr': True,
    # pyqode is a namespace package bundled in open_cobol_ide/extlibs
    'packages': ['pyqode', 'pyqode.core', 'pyqode.cobol', 'pyqode.qt'],
    # freeze the pygments default style along with our executable
    'includes': pygments_styles + pygments_lexers
}

if windows:
    options['includes'].append('win32timezone')


print(
    '### Freezing application\n'
    '#####################################################################\n')
setup(name=app_name,
      version=version,
      options={'build_exe': options, 'bdist_mac': {
          'iconfile': app_icon, 'custom_info_plist': 'share/Info.plist'}},
      executables=[
          Executable(app_script,
                     target_name=app_exe,
                     icon=app_icon if windows else None,
                     base='gui' if windows else None),
          Executable(console_script, target_name=console_exe),
          Executable(srv_script, target_name=srv_exe),
          Executable(core_srv_script, target_name=core_srv_exe)
      ])

if windows:
    build_dir = os.path.join(os.getcwd(), glob.glob('build/*')[0])
    # cx_freeze may miss libEGL.dll, copy it ourself when we can find it
    # (recent PyQt5 wheels keep it in PyQt5/Qt5/bin instead of PyQt5/)
    try:
        import PyQt5
        qt_dir = os.path.dirname(PyQt5.__file__)
        for folder in (qt_dir, os.path.join(qt_dir, 'Qt5', 'bin')):
            egl = os.path.join(folder, 'libEGL.dll')
            if os.path.exists(egl):
                shutil.copy(egl, build_dir)
                break
    except (ImportError, RuntimeError):
        pass  # nothing to do
    if os.path.exists(r'c:\DigiCertUtil.exe'):
        print('Signing our binaries\n'
              '#################################################################')
        for executable in ['OpenCobolIDE.exe', 'cobol-backend.exe',
                           'core-backend.exe', 'pyqode-console.exe']:
            path = os.path.join(build_dir, executable)
            os.system(r'c:\DigiCertUtil.exe sign /noInput %s' % path)
    print(
        '### Copying OpenCobol distribution\n'
        '#####################################################################'
        '\n')
    cobc_dir = os.path.join(build_dir, 'GnuCOBOL')
    if not os.path.exists(cobc_dir):
        shutil.copytree(os.path.join(os.getcwd(), 'GnuCOBOL-Win32-MinGW'),
                        os.path.join(build_dir, 'GnuCOBOL'))

    # translations (read at run time by open_cobol_ide.translation)
    tr_dir = os.path.join(build_dir, 'translations')
    if not os.path.exists(tr_dir):
        shutil.copytree(
            os.path.join(os.getcwd(), 'open_cobol_ide', 'translations'),
            tr_dir)

    print(
        '\n### Creating windows installer using Inno Setup\n'
        '#####################################################################'
        '\n')
    build_dir = glob.glob('build/*')[0]
    with open('scripts/setup.iss.in', 'r') as src, \
            open('setup.iss', 'w') as dst:
        lines = src.readlines()
        data = []
        for l in lines:
            l = l.replace('@VERSION@', version)
            l = l.replace('@BUILD_DIR@', build_dir)
            data.append(l)
        dst.writelines(data)
    # if not in PATH, inno setup is usually located in
    # C:\Program Files (x86)\Inno Setup 5 on Windows
    os.environ['PATH'] += r';C:\Program Files (x86)\Inno Setup 5'
    if shutil.which('iscc'):
        os.system('iscc %s' % os.path.join(os.getcwd(), 'setup.iss'))
    else:
        print('Inno Setup not found: skipping installer creation '
              '(the portable build is in %s)' % build_dir)
