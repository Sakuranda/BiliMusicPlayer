#!/usr/bin/env python3
"""Package tracked public source only, never a working directory or runtime data."""
import hashlib
import shutil
import subprocess
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
out = root / 'release-dist'
out.mkdir(exist_ok=True)
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
forbidden = {'.git', 'data', 'backups', 'node_modules', '.venv', '__pycache__', 'release-dist'}
archive = out / 'bilimusicplayer.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    for name in sorted(filter(None, files)):
        path = Path(name)
        if forbidden.intersection(path.parts) or path.name == '.env' or path.suffix in {'.db', '.bak', '.m4a', '.mp3', '.flac'}:
            raise SystemExit('Private/runtime file tracked: ' + name)
        source = root / name
        if source.is_symlink():
            raise SystemExit('Unexpected symlink: ' + name)
        tar.add(source, arcname='BiliMusicPlayer/' + name, recursive=False)
shutil.copy2(root / 'deploy/install.sh', out / 'install.sh')
(out / 'SHA256SUMS').write_text(''.join(hashlib.sha256((out/name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in ('bilimusicplayer.tar.gz', 'install.sh')))
print('Release assets:', out)
