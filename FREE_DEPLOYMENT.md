# Free-first deployment

## Recommended path
1. Create a GitHub repository and push this project.
2. Deploy `app/main.py` with Streamlit Community Cloud.
3. Leave `APP_STORAGE_BACKEND` unset to use local SQLite/files for the simplest demo.
4. For persistent multi-session cloud storage, configure Supabase using `secrets.example.toml` as the template.
5. Do not commit real secrets.

## No paid AI required
If `PRODUCT_BUILDER_API_KEY` / `OPENAI_API_KEY` is absent, product blueprint and section generation use deterministic local templates. The research engine, scoring, editing, QA, and PDF export remain usable.

## Optional services
- OpenAI-compatible provider: optional, not required for free mode.
- YouTube transcript package: optional; YouTube search still works without transcripts.
- Supabase: optional; local SQLite/files are the default.

## Local run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/doctor.py
python free_mode_test.py
streamlit run app/main.py
```

The auth cookie is configured as secure because the hosted deployment should use HTTPS.

## Evidence note
Quora and Instagram collectors use public search-index snippets rather than private APIs. They are discovery signals and must be manually validated before being treated as evidence.
