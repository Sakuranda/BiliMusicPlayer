import time
import unittest
from unittest.mock import Mock, patch

from app import auth


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        auth._captchas.clear()
        auth._failures.clear()
        auth._locked_until.clear()
        auth._last_touch.clear()

    def test_captcha_is_one_time_and_case_insensitive(self):
        challenge = auth.create_captcha()
        answer = auth._captchas[challenge["id"]][0]
        self.assertTrue(challenge["image"].startswith("data:image/png;base64,"))
        self.assertTrue(auth.consume_captcha(challenge["id"], answer.lower()))
        self.assertFalse(auth.consume_captcha(challenge["id"], answer))

    def test_signed_session_rejects_tampering(self):
        token, session_id = auth.create_session("admin")
        payload = auth.verify_session(token)
        self.assertEqual(payload["sid"], session_id)
        self.assertEqual(payload["sub"], "admin")
        self.assertIsNone(auth.verify_session(token + "broken"))

    def test_login_failures_are_rate_limited(self):
        for _ in range(auth.FAIL_LIMIT):
            auth.record_failure("203.0.113.7")
        allowed, retry_after = auth.login_allowed("203.0.113.7")
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        auth.clear_failures("203.0.113.7")
        self.assertTrue(auth.login_allowed("203.0.113.7")[0])

    def test_forwarded_ip_only_trusted_from_private_proxy(self):
        self.assertEqual(auth.client_ip("172.18.0.2", "8.8.8.8, 172.18.0.1"), "8.8.8.8")
        self.assertEqual(auth.client_ip("8.8.4.4", "8.8.8.8"), "8.8.4.4")

    def test_credentials_use_configured_account(self):
        with patch.multiple(auth, ADMIN_USERNAME="owner", ADMIN_PASSWORD="secret"):
            self.assertTrue(auth.credentials_valid("owner", "secret"))
            self.assertFalse(auth.credentials_valid("owner", "wrong"))

    def test_unicode_input_fails_cleanly_and_unicode_password_works(self):
        with patch.multiple(auth, ADMIN_USERNAME="owner", ADMIN_PASSWORD="中文密码"):
            self.assertTrue(auth.credentials_valid("owner", "中文密码"))
            self.assertFalse(auth.credentials_valid("陌生用户", "错误密码"))
        token, _ = auth.create_session("admin")
        self.assertIsNone(auth.verify_session(token + "中文"))
        challenge = auth.create_captcha()
        self.assertFalse(auth.consume_captcha(challenge["id"], "你好世界"))

    def test_access_touch_is_throttled(self):
        self.assertTrue(auth.should_touch("session"))
        self.assertFalse(auth.should_touch("session"))
        auth._last_touch["session"] = time.time() - 61
        self.assertTrue(auth.should_touch("session"))

    def test_public_ip_location_is_normalized(self):
        response = Mock()
        response.json.return_value = {
            "success": True,
            "country": "China",
            "region": "Hong Kong",
            "city": "Hong Kong",
        }
        with patch.object(auth.httpx, "get", return_value=response) as request:
            location = auth.lookup_location("8.8.8.8")
        request.assert_called_once()
        self.assertEqual(location, {
            "country": "China",
            "region": "Hong Kong",
            "city": "Hong Kong",
        })


if __name__ == "__main__":
    unittest.main()
