import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app import admin, auth, db, main


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(db, 'DB_PATH', self.root/'test.db'), patch.object(db, 'DATA_DIR', self.root),
                        patch.object(db, 'MUSIC_DIR', self.root/'music'), patch.object(db, 'COVER_DIR', self.root/'covers'),
                        patch.object(db, '_initialized', False), patch.object(admin, 'ADMIN_USERNAME', 'admin'),
                        patch.object(admin, 'ADMIN_PASSWORD', 'initial-password'), patch.object(auth, 'ADMIN_USERNAME', 'admin'),
                        patch.object(auth, 'ADMIN_PASSWORD', 'initial-password'), patch.object(main, 'API_TOKEN', 'script-token')]
        for p in self.patches: p.start()
        auth._failures.clear(); auth._locked_until.clear()
        self.client = TestClient(main.app)
        token, _ = auth.create_session('admin')
        self.client.cookies.set(auth.COOKIE_NAME, token)
        self.initial_token = token

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def change(self, action, **values):
        return admin.change(action, 'initial-password', values.pop('code', ''), values.pop('epoch', 'initial'), **values)

    def test_rfc6238_sha1_vectors(self):
        key = base64.b32encode(b'12345678901234567890').decode()
        for timestamp, expected in [(59,'94287082'), (1111111109,'07081804'), (1111111111,'14050471'),
                                    (1234567890,'89005924'), (2000000000,'69279037'), (20000000000,'65353130')]:
            self.assertEqual(admin.totp(key, timestamp//30, 8), expected)

    def test_account_change_persists_hash_and_revokes_old_session(self):
        result = self.change('account', username='新的管理员', new_password='a-new-safe-password')
        row = admin.account()
        self.assertNotIn('a-new-safe-password', row['password_hash'])
        self.assertTrue(auth.credentials_valid('新的管理员', 'a-new-safe-password'))
        self.assertFalse(auth.credentials_valid('admin','initial-password'))
        self.assertIsNone(auth.verify_session(self.initial_token))
        self.assertEqual(admin.settings()['username'], '新的管理员')
        token,_ = auth.create_session('新的管理员', result['_epoch'])
        self.assertIsNotNone(auth.verify_session(token))

    def test_setup_requires_valid_code_and_expires(self):
        self.change('setup')
        self.assertFalse(admin.settings()['two_factor_enabled'])
        with self.assertRaises(ValueError): self.change('enable', code='00000')
        with patch.object(admin.time, 'time', return_value=admin.account()['pending_since']+601):
            with self.assertRaises(ValueError): self.change('enable', code='123456')
        self.assertFalse(admin.settings()['two_factor_enabled'])

    def enable(self):
        result = self.change('setup')
        row = admin.account()
        counter = int(admin.time.time())//30
        code = admin.totp(result['secret'], counter)
        result = self.change('enable', code=code)
        return row['pending_secret'], counter, result['_epoch']

    def test_totp_required_no_replay_and_disable_requires_totp(self):
        secret, counter, epoch = self.enable()
        self.assertIsNone(admin.authenticate('admin','initial-password',''))
        with patch.object(admin.time,'time', return_value=(counter+1)*30+1):
            code = admin.totp(secret,counter+1)
            self.assertEqual(admin.authenticate('admin','initial-password',code),epoch)
            self.assertIsNone(admin.authenticate('admin','initial-password',code))
            with self.assertRaises(ValueError): self.change('disable',epoch=epoch,code=code)
        with patch.object(admin.time,'time', return_value=(counter+2)*30+1):
            self.change('disable',epoch=epoch,code=admin.totp(secret,counter+2))
        self.assertFalse(admin.settings()['two_factor_enabled'])
        self.assertIsNotNone(admin.authenticate('admin','initial-password',''))

    def test_concurrent_totp_is_accepted_only_once(self):
        secret, counter, epoch = self.enable()
        with patch.object(admin.time, 'time', return_value=(counter+1)*30+1):
            code = admin.totp(secret,counter+1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: admin.authenticate('admin','initial-password',code), range(2)))
        self.assertEqual(results.count(epoch),1)

    def test_security_api_needs_browser_and_current_password(self):
        wrong = self.client.put('/api/auth/settings', json={'current_password':'wrong','username':'new'})
        self.assertEqual(wrong.status_code,400)
        self.client.cookies.clear()
        token_only = self.client.put('/api/auth/settings', headers={'X-Api-Token':'script-token'},json={'current_password':'initial-password'})
        self.assertEqual(token_only.status_code,401)
        self.assertEqual(self.client.get('/api/auth/settings').status_code,401)

    def test_security_api_rotates_cookie_and_does_not_leak_secret(self):
        response = self.client.put('/api/auth/settings',json={'current_password':'initial-password','username':'owner','new_password':'new-password-123'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertTrue('HttpOnly' in response.headers['set-cookie'])
        response = self.client.get('/api/auth/settings')
        self.assertEqual(response.json(), {'username':'owner','two_factor_enabled':False})
        self.assertNotIn('password_hash',response.text)
        self.assertEqual(self.client.get('/api/auth/status').json()['username'],'owner')

    def test_login_requires_second_factor_through_api(self):
        secret, counter, _ = self.enable()
        self.client.cookies.clear()
        def login(code):
            challenge = auth.create_captcha()
            answer = auth._captchas[challenge['id']][0]
            with patch.object(auth,'lookup_location',return_value={'country':None,'region':None,'city':None}):
                return self.client.post('/api/auth/login',json={'username':'admin','password':'initial-password','captcha_id':challenge['id'],'captcha':answer,'totp_code':code})
        self.assertEqual(login('').status_code,401)
        with patch.object(admin.time, 'time', return_value=(counter+1)*30+1):
            code = admin.totp(secret,counter+1)
            self.assertEqual(login(code).status_code,200)
            self.assertEqual(login(code).status_code,401)

    def test_short_password_and_stale_epoch_cannot_modify(self):
        with self.assertRaises(ValueError): self.change('account',new_password='short')
        self.assertIsNone(admin.account())
        self.change('account',username='owner')
        with self.assertRaises(ValueError): self.change('disable')
