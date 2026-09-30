import unittest

from app.ui.web_interface import AUTH_COOKIE, _select_auth_token


class ProductAuthTests(unittest.TestCase):
    def test_request_cookie_is_authoritative_when_context_is_available(self):
        token = _select_auth_token(
            {AUTH_COOKIE: "request-token"},
            {AUTH_COOKIE: "component-token"},
            context_available=True,
        )
        self.assertEqual(token, "request-token")

    def test_component_cookie_is_fallback_when_request_cookie_is_missing(self):
        token = _select_auth_token(
            {},
            {AUTH_COOKIE: "component-token"},
            context_available=True,
        )
        self.assertEqual(token, "component-token")

    def test_component_cookie_is_used_only_when_request_context_is_unavailable(self):
        token = _select_auth_token(
            None,
            {AUTH_COOKIE: "component-token"},
            context_available=False,
        )
        self.assertEqual(token, "component-token")

    def test_missing_or_non_string_tokens_are_ignored(self):
        self.assertEqual(
            _select_auth_token({AUTH_COOKIE: ""}, {AUTH_COOKIE: "fallback"}, context_available=True),
            "fallback",
        )
        self.assertEqual(
            _select_auth_token({AUTH_COOKIE: 123}, {AUTH_COOKIE: "fallback"}, context_available=True),
            "fallback",
        )
        self.assertIsNone(
            _select_auth_token({AUTH_COOKIE: ""}, {AUTH_COOKIE: 123}, context_available=True)
        )


if __name__ == "__main__":
    unittest.main()
