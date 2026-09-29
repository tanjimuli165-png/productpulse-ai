from pathlib import Path
from tempfile import TemporaryDirectory

from app.database.db import ReportStore

with TemporaryDirectory() as directory:
    store = ReportStore(Path(directory) / "cookie.db")
    ok, _ = store.register_user("cookieuser", "correct-horse-123")
    assert ok
    user = store.authenticate("cookieuser", "correct-horse-123")
    token = store.create_session(user["user_id"], days=30)
    assert store.authenticate_session(token)["username"] == "cookieuser"
    store.revoke_session(token)
    assert store.authenticate_session(token) is None

ui = Path("app/ui/web_interface.py").read_text()
requirements = Path("requirements.txt").read_text()
assert "extra_streamlit_components" in ui
assert "CookieManager" in ui
assert "expires_at" in ui
assert "cookies.delete(AUTH_COOKIE" in ui
assert "extra-streamlit-components>=0.1.81" in requirements
assert "localStorage" not in ui
print("V2.3 CookieManager smoke test passed")
