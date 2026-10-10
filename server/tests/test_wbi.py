import unittest
from unittest.mock import patch
import httpx
from app import bilibili

NAV = {'code':-101,'data':{'wbi_img':{
    'img_url':'https://i0.hdslb.com/bfs/wbi/0123456789abcdef0123456789abcdef.png',
    'sub_url':'https://i0.hdslb.com/bfs/wbi/fedcba9876543210fedcba9876543210.png',
}}}
AUDIO = {'dash':{'audio':[{'id':30280}]},'timelength':100000}


class WBIPlayurlTests(unittest.TestCase):
    def setUp(self):
        bilibili._wbi_cache.update(key=None, expires=0.0, retry_at=0.0)

    def client_factory(self, respond):
        clients = []
        def factory(cookie=None):
            headers = dict(bilibili.BILI_HEADERS)
            if cookie:
                headers['Cookie'] = cookie
            client = httpx.Client(headers=headers, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client
        return factory, clients

    def test_normal_old_api_does_not_get_nav_or_signed_api(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'code':0,'data':AUDIO})
        factory, clients = self.client_factory(respond)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle') as throttle:
            self.assertEqual(bilibili.fetch_playurl('BVTEST',123), AUDIO)
        self.assertEqual([r.url.path for r in requests], ['/x/player/playurl'])
        self.assertEqual(throttle.call_count, 1)
        self.assertTrue(all(c.is_closed for c in clients))

    def test_network_http_and_api_failures_fallback_without_view_preserving_cookie_and_throttle(self):
        for failure in ('network','http','code'):
            with self.subTest(failure=failure):
                bilibili._wbi_cache.update(key=None, expires=0.0, retry_at=0.0)
                requests=[]
                def respond(request):
                    requests.append(request)
                    if request.url.path == '/x/player/playurl':
                        if failure=='network':
                            raise httpx.ConnectError('secret should never be printed',request=request)
                        return httpx.Response(412) if failure=='http' else httpx.Response(200,json={'code':-403})
                    if request.url.path.endswith('/nav'):
                        return httpx.Response(200,json=NAV)
                    self.assertEqual(request.url.params['cid'],'123')
                    self.assertEqual(request.url.params['bvid'],'BVTEST')
                    self.assertIn('w_rid',request.url.params)
                    return httpx.Response(200,json={'code':0,'data':AUDIO})
                factory, clients=self.client_factory(respond)
                with patch.object(bilibili,'_client',side_effect=factory) as create, patch.object(bilibili,'_throttle') as throttle:
                    self.assertEqual(bilibili.fetch_playurl('BVTEST',123,'SESSDATA=fake-test-cookie'),AUDIO)
                create.assert_called_once_with('SESSDATA=fake-test-cookie')
                self.assertEqual(throttle.call_count, 3)
                self.assertEqual([r.url.path for r in requests],['/x/player/playurl','/x/web-interface/nav','/x/player/wbi/playurl'])
                for request in requests:
                    self.assertEqual(request.headers['Cookie'],'SESSDATA=fake-test-cookie')
                    self.assertEqual(request.headers['Referer'],'https://www.bilibili.com/')
                self.assertTrue(all(c.is_closed for c in clients))

    def test_missing_keys_fail_friendly_and_cooldown_does_not_slide_on_repeated_failures(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(412) if request.url.path.endswith('/playurl') else httpx.Response(200,json={'code':-101,'data':{}})
        factory, clients=self.client_factory(respond)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle'), patch.object(bilibili.time,'monotonic',return_value=100):
            with self.assertRaisesRegex(bilibili.BiliError,'WBI签名信息'):
                bilibili.fetch_playurl('BVTEST',123)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle'), patch.object(bilibili.time,'monotonic',return_value=110):
            with self.assertRaisesRegex(bilibili.BiliError,'暂不可用'):
                bilibili.fetch_playurl('BVTEST',123)
        self.assertEqual(bilibili._wbi_cache['retry_at'],160)
        self.assertEqual([r.url.path for r in requests],['/x/player/playurl','/x/web-interface/nav','/x/player/playurl'])
        self.assertTrue(all(c.is_closed for c in clients))

    def test_key_cache_reuses_public_keys_and_refreshes_after_expiry(self):
        requests=[]
        def respond(request):
            requests.append(request.url.path)
            if request.url.path=='/x/player/playurl':
                return httpx.Response(412)
            return httpx.Response(200,json=NAV if request.url.path.endswith('/nav') else {'code':0,'data':AUDIO})
        factory, clients=self.client_factory(respond)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle'), patch.object(bilibili.time,'monotonic',return_value=100):
            bilibili.fetch_playurl('BVTEST',123)
            bilibili.fetch_playurl('BVOTHER',456)
        self.assertEqual(requests.count('/x/web-interface/nav'),1)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle'), patch.object(bilibili.time,'monotonic',return_value=3701):
            bilibili.fetch_playurl('BVTHIRD',789)
        self.assertEqual(requests.count('/x/web-interface/nav'),2)
        self.assertTrue(all(c.is_closed for c in clients))

    def test_signature_matches_known_frozen_time_vector_without_mutating_input(self):
        params={'foo':"a!b(c)*d"}
        with patch.object(bilibili.time,'time',return_value=1700000000):
            signed=bilibili._sign_wbi(params,'0123456789abcdef0123456789abcdef')
        self.assertEqual(params,{'foo':"a!b(c)*d"})
        self.assertEqual(signed,{'foo':'abcd','wts':'1700000000','w_rid':'cce3be5fdd20053a6e66d25166600a1d'})

    def test_signed_request_network_error_never_exposes_url_or_cookie(self):
        def respond(request):
            if request.url.path=='/x/player/playurl':
                return httpx.Response(412)
            if request.url.path.endswith('/nav'):
                return httpx.Response(200,json=NAV)
            raise httpx.ReadTimeout('signed secret '+str(request.url)+' cookie fake-test-cookie',request=request)
        factory, clients=self.client_factory(respond)
        with patch.object(bilibili,'_client',side_effect=factory), patch.object(bilibili,'_throttle'):
            with self.assertRaises(bilibili.BiliError) as raised:
                bilibili.fetch_playurl('BVTEST',123,'SESSDATA=fake-test-cookie')
        self.assertNotIn('w_rid',str(raised.exception))
        self.assertNotIn('fake-test-cookie',str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(all(c.is_closed for c in clients))
