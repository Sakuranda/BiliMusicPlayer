import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'app'
        shutil.copytree(ROOT/'deploy',self.root/'deploy',ignore=shutil.ignore_patterns('tests','.env'))
    def tearDown(self): self.temp.cleanup()
    def run_script(self,*args):
        return subprocess.run(['bash',str(self.root/'deploy/deploy.sh'),*args],capture_output=True,text=True)
    def configure(self):
        result=self.run_script('--domain','music.example.com','--navidrome-domain','navi.example.com','--configure-only')
        self.assertEqual(result.returncode,0,result.stderr)
        return self.root/'deploy/.env'
    def test_generated_configuration_is_private_and_auth_is_enabled(self):
        config=self.configure()
        values=dict(line.split('=',1) for line in config.read_text().splitlines() if '=' in line and not line.startswith('#'))
        self.assertEqual(config.stat().st_mode&0o777,0o600)
        self.assertEqual(len(values['ADMIN_PASSWORD']),32)
        self.assertEqual(len(values['AUTH_SECRET']),64)
        self.assertEqual(values['API_TOKEN'],'')
        self.assertEqual(values['COMPOSE_PROFILES'],'https')
        self.assertTrue((self.root/'data/music/MusicPlayer-Playlists').is_dir())
    def test_upgrade_keeps_credentials_and_media(self):
        config=self.configure(); before=config.read_bytes()
        media=self.root/'data/music/keep.m4a'; media.write_bytes(b'original')
        self.assertEqual(self.run_script('--configure-only').returncode,0)
        self.assertEqual(config.read_bytes(),before)
        self.assertEqual(self.run_script('--domain','music.example.com','--navidrome-domain','navi.example.com','--configure-only').returncode,0)
        self.assertEqual(config.read_bytes(),before)
        self.assertEqual(media.read_bytes(),b'original')
        self.assertNotEqual(self.run_script('--domain','other.example.com','--configure-only').returncode,0)
        self.assertEqual(config.read_bytes(),before)
    def test_invalid_domain_does_not_create_config(self):
        for domain in ('https://music.example.com','music.example.com/path','$(touch /tmp/oops)','localhost','-music.example.com'):
            self.assertNotEqual(self.run_script('--domain',domain,'--navidrome-domain','navi.example.com','--configure-only').returncode,0)
        self.assertFalse((self.root/'deploy/.env').exists())
    def test_existing_proxy_mode_does_not_expose_ports(self):
        result=self.run_script('--domain','music.example.com','--navidrome-domain','navi.example.com','--no-https','--configure-only')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('COMPOSE_PROFILES=\n',(self.root/'deploy/.env').read_text())

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        import hashlib, tarfile, io
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.fixture=self.root/'fixture'; self.fixture.mkdir()
        self.bin=self.root/'bin'; self.bin.mkdir()
        self.target=self.root/'installation'
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'FIXTURE':str(self.fixture)}
        (self.bin/'curl').write_text('#!/usr/bin/env bash\nset -e\nurl= out=\nwhile (($#)); do case "$1" in -o) out=$2;shift 2;; https://*) url=$1;shift;; *) shift;; esac;done\ncp "$FIXTURE/${url##*/}" "$out"\n')
        (self.bin/'curl').chmod(0o755)
        # GNU tar is required by production; macOS tests use gtar when available.
        if os.uname().sysname=='Darwin':
            tar=shutil.which('gtar')
            if tar:
                (self.bin/'tar').symlink_to(tar)
            else:
                (self.bin/'tar').write_text('#!/usr/bin/env bash\nargs=(); for a in "$@"; do [[ "$a" == --no-same-permissions ]] || args+=("$a"); done\nexec /usr/bin/tar "${args[@]}"\n')
                (self.bin/'tar').chmod(0o755)
        script=b'#!/usr/bin/env bash\ncd "$(dirname "$0")/.."\nmkdir -p data; printf ready > data/marker\n'
        with tarfile.open(self.fixture/'bilimusicplayer.tar.gz','w:gz') as archive:
            for name,data in [('BiliMusicPlayer/deploy/deploy.sh',script),('BiliMusicPlayer/VERSION',b'1.0.0\n')]:
                info=tarfile.TarInfo(name); info.size=len(data); archive.addfile(info,io.BytesIO(data))
        self.checksum()
    def tearDown(self): self.temp.cleanup()
    def checksum(self):
        import hashlib
        (self.fixture/'SHA256SUMS').write_text(hashlib.sha256((self.fixture/'bilimusicplayer.tar.gz').read_bytes()).hexdigest()+'  bilimusicplayer.tar.gz\n')
    def run_bootstrap(self):
        return subprocess.run(['bash',str(ROOT/'deploy/install.sh'),'--dir',str(self.target)],env=self.env,capture_output=True,text=True)
    def test_verified_release_installs_into_requested_directory(self):
        result=self.run_bootstrap()
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual((self.target/'data/marker').read_text(),'ready')
    def test_bad_checksum_does_not_touch_target(self):
        (self.fixture/'SHA256SUMS').write_text('0'*64+'  bilimusicplayer.tar.gz\n')
        self.assertNotEqual(self.run_bootstrap().returncode,0)
        self.assertFalse(self.target.exists())
    def test_path_traversal_and_private_paths_rejected_before_install(self):
        import tarfile,io
        for name in ('BiliMusicPlayer/../../outside','BiliMusicPlayer/data/musicplayer.db','BiliMusicPlayer/deploy/.env'):
            with tarfile.open(self.fixture/'bilimusicplayer.tar.gz','w:gz') as archive:
                info=tarfile.TarInfo(name); info.size=4; archive.addfile(info,io.BytesIO(b'nope'))
            self.checksum()
            self.assertNotEqual(self.run_bootstrap().returncode,0)
            self.assertFalse(self.target.exists())
    def test_non_application_directory_is_preserved(self):
        self.target.mkdir(); sentinel=self.target/'existing'; sentinel.write_text('keep')
        self.assertNotEqual(self.run_bootstrap().returncode,0)
        self.assertEqual(sentinel.read_text(),'keep')
