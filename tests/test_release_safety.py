"""Run packaging path tests only inside disposable project fixtures."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReleaseSafety(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / 'tests' / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.project = Path(self.temp.name)
        self.installer = self.project / 'installer'
        self.installer.mkdir()
        for name in ['build.ps1', 'verify.ps1', 'messages.json', 'path-safety.ps1']:
            source = ROOT / 'installer' / name
            if source.exists(): shutil.copy2(source, self.installer / name)
        (self.project / 'src/picfinder').mkdir(parents=True)
        (self.project / 'src/picfinder/__init__.py').write_text('__version__ = "0.1.6"')
        (self.project / 'assets').mkdir()
        (self.project / 'assets/picfinder.ico').write_bytes(b'fixture')
        mock = self.project / 'PyInstaller'
        mock.mkdir()
        (mock / '__init__.py').write_text('')
        (mock / '__main__.py').write_text(
            'import sys\nfrom pathlib import Path\n'
            'root=Path(sys.argv[sys.argv.index("--distpath")+1])/"PIC FINDER"\n'
            'd=root/"_internal/dictionary/zh"\nd.mkdir(parents=True,exist_ok=True)\n'
            '(root/"PIC FINDER.exe").write_bytes(b"not an executable")\n'
            '(d/"general.csv").write_text("fixture")\n'
            '(d.parent/"manifest.json").write_text("{}")\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, name, *args):
        env = dict(os.environ, PYTHONPATH=str(self.project), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(self.installer / name), *map(str, args)],
                              cwd=self.project, env=env, capture_output=True, timeout=30)

    def test_B22_clean_rejected_without_touching_old_distribution(self):
        sentinel = self.installer / 'dist/old-portable/data/keep.txt'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text('user data')
        result = self.invoke('build.ps1', '-Python', sys.executable, '-Clean')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'user data')

    def test_B22_repeated_builds_have_distinct_output_directories(self):
        results = []
        for _ in range(2):
            result = self.invoke('build.ps1', '-Python', sys.executable)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            report = json.loads((self.installer / 'build-report.json').read_text(encoding='utf-8-sig'))
            results.append(Path(report['app_directory']))
        self.assertNotEqual(*results)
        self.assertTrue(all((path / 'PIC FINDER.exe').exists() for path in results))

    def test_B23_existing_verification_base_is_never_deleted(self):
        app = self.installer / 'dist/PIC FINDER'
        app.mkdir(parents=True)
        (app / 'PIC FINDER.exe').write_bytes(b'not an executable')
        (self.installer / 'build-report.json').write_text(json.dumps({'app_directory': str(app)}))
        base = self.project / 'existing-data'
        base.mkdir()
        sentinel = base / 'keep.txt'
        sentinel.write_text('user data')
        result = self.invoke('verify.ps1', '-DataRoot', 'existing-data')
        self.assertNotEqual(result.returncode, 0)  # Fake executable deliberately cannot start.
        self.assertEqual(sentinel.read_text(), 'user data')

    def test_owned_cleanup_checks_token_and_path_boundary(self):
        helper = self.installer / 'path-safety.ps1'
        self.assertTrue(helper.exists())
        script = self.project / 'exercise-safety.ps1'
        script.write_text('''$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installer/path-safety.ps1')
$base = Join-Path $PSScriptRoot 'owned'
$run = New-OwnedDirectory $base 'verify'
$sentinel = Join-Path $run.Path 'keep.txt'
Set-Content -LiteralPath $sentinel -Value 'test'
$rejected = $false
try { Remove-OwnedDirectory $base $run.Path 'wrong-token' } catch { $rejected = $true }
if (-not $rejected -or -not (Test-Path -LiteralPath $sentinel)) { throw 'Token guard failed' }
foreach ($bad in @($base, $PSScriptRoot, (Join-Path $base '../outside'))) {
    $rejected = $false
    try { Get-ContainedDirectory $base $bad | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Boundary guard failed' }
}
Remove-OwnedDirectory $base $run.Path $run.Token
if (Test-Path -LiteralPath $run.Path) { throw 'Owned directory was not cleaned' }
''', encoding='ascii')
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

if __name__ == '__main__':
    unittest.main()
