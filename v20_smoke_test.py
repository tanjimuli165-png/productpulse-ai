from pathlib import Path

source = Path("app/ui/web_interface.py").read_text()
assert 'st.tabs(["Log in", "Register"])' in source
assert 'st.sidebar.tabs(["Log in", "Register"])' not in source
assert 'st.sidebar.subheader("Account")' in source
assert 'st.sidebar.button("Log out"' in source
assert 'st.warning("Please log in to use the opportunity engine.")' in source
print("V2.0 mobile auth UI smoke test passed")
