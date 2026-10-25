"""Build from an sdist, install into an empty target, and check bundled resources."""
import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
env = dict(os.environ, PYTHONIOENCODING='utf-8', PIP_DISABLE_PIP_VERSION_CHECK='1',
           PIP_NO_INDEX='1', QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1')
def run(command):
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    with (output / 'commands.txt').open('a', encoding='utf-8') as stream:
        stream.write(result.stdout + '\n')
    if result.returncode:
        raise RuntimeError(result.stdout[-5000:])
run([sys.executable, '-c', 'from setuptools.build_meta import build_sdist; build_sdist('+repr(str(output))+')'])
sdist = next(output.glob('*.tar.gz'))
run([sys.executable, '-m', 'pip', 'wheel', str(sdist), '--no-deps', '--no-build-isolation',
     '--no-index', '--wheel-dir', str(output)])
wheel = next(output.glob('*.whl'))
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    resources = [name for name in names if name.startswith('picfinder/resources/dictionary/')]
    assert len(resources) == 11, resources
target = output / 'installed'
run([sys.executable, '-m', 'pip', 'install', str(wheel), '--no-deps', '--no-index', '--no-compile',
     '--target', str(target)])
probe = '''import sys,runpy
from pathlib import Path
sys.path.insert(0, TARGET)
from picfinder.translate.dictionary import builtin_source_dir
directory=builtin_source_dir()
assert directory and directory.is_relative_to(Path(TARGET)), directory
assert len(list((directory/'zh').glob('*.csv')))==5
sys.argv=['picfinder','--check','--data-root',DATA,'--report',REPORT]
runpy.run_module('picfinder',run_name='__main__')
'''.replace('TARGET', repr(str(target))).replace('DATA', repr(str(output/'run'))).replace('REPORT', repr(str(output/'startup-check.txt')))
run([sys.executable, '-I', '-X', 'utf8', '-c', probe])
(output/'result.json').write_text(json.dumps({'wheel':wheel.name,'bundled_files':len(resources),
    'installed_self_check':'passed'},indent=2),encoding='utf-8')
print('Wheel from sdist: PASS; bundled dictionary files: 11; isolated installed self-check: PASS')
