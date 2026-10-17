import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import WebSocketDisconnect
from bebraland_backend import api, auth, config, skins, yggdrasil


class SiteUrlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"BEBRALAND_SITE_URL": "https://new.example/site/", "AZURIOM_URL": "https://old.example"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_site_setting_controls_auth_and_skins(self):
        self.assertEqual(config.site_url(), "https://new.example/site")
        self.assertEqual(auth.azuriom_base_url(), "https://new.example/site/")
        self.assertEqual(skins.skin_api_url("api/skin-api/profile/test"), "https://new.example/site/api/skin-api/profile/test")

    def test_legacy_azuriom_setting_is_still_supported(self):
        del os.environ["BEBRALAND_SITE_URL"]
        self.assertEqual(auth.azuriom_base_url(), "https://old.example/")

    def test_missing_site_is_reported(self):
        os.environ.pop("BEBRALAND_SITE_URL")
        os.environ.pop("AZURIOM_URL")
        with self.assertRaises(auth.AzuriomAuthError):
            auth.azuriom_base_url()

    def response(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{}'
        return response

    def test_auth_request_uses_current_domain_and_user_agent(self):
        with patch.object(auth.urllib.request, "urlopen", return_value=self.response()) as opened:
            auth.azuriom_post("authenticate", {"email": "test", "password": "test"})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://new.example/site/api/auth/authenticate")
        self.assertEqual(request.get_header("User-agent"), config.SITE_USER_AGENT)

    def test_skin_request_uses_site_user_agent(self):
        with patch.object(skins.urllib.request, "urlopen", return_value=self.response()) as opened:
            skins.get_json("api/skin-api/profile/test")
        self.assertEqual(opened.call_args.args[0].get_header("User-agent"), config.SITE_USER_AGENT)

    def test_yggdrasil_site_request_uses_domain_and_user_agent(self):
        with patch.object(yggdrasil.urllib.request, "urlopen", return_value=self.response()) as opened:
            yggdrasil._azuriom_get_json("api/skin-api/profile/test")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://new.example/site/api/skin-api/profile/test")
        self.assertEqual(request.get_header("User-agent"), config.SITE_USER_AGENT)

    def test_public_metadata_contains_site_url(self):
        self.assertEqual(json.loads(api.root().body)["site_url"], "https://new.example/site")

    async def test_websocket_greeting_contains_site_url(self):
        websocket = Mock()
        websocket.receive_json = AsyncMock(side_effect=WebSocketDisconnect)
        with patch.object(api.manager, "connect", new_callable=AsyncMock), patch.object(api.manager, "send", new_callable=AsyncMock) as send, patch.object(api.manager, "disconnect", new_callable=AsyncMock):
            await api.websocket_api(websocket)
        greeting = send.call_args.args[1]
        self.assertEqual(greeting["type"], "hello")
        self.assertEqual(greeting["site_url"], "https://new.example/site")


if __name__ == "__main__":
    unittest.main()
