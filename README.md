## Free-first mode

The project runs without a paid AI key. If no AI provider key is configured, blueprint and section generation use deterministic local templates. Quora/Instagram discovery uses public search-index snippets, and YouTube transcripts are optional. See `FREE_DEPLOYMENT.md`.

# Global Digital Product Opportunity Engine — V1

A Streamlit research automation system with a safe local-first mode and an optional private Supabase cloud backend that turns a niche, topic, or problem statement into a downloadable PDF report of digital product hypotheses. The report combines public evidence collection, problem-language extraction, narrowly matched payment-language proxies, competitor-gap heuristics, product-system architecture, and a transparent prioritization score.

## What it does

Enter a topic such as `onboarding systems for independent consultants`, select public sources, and click **Run opportunity scan**. The app collects Reddit search results, YouTube search results, and general web results in parallel. It normalizes and deduplicates the evidence, extracts explicit problem-language patterns, detects qualifying first-person payment statements, identifies weakness themes, ranks up to five product hypotheses, stores the report in the configured database and generates a styled PDF in `data/reports/` (ephemeral on Community Cloud).

The engine is deliberately transparent. Public search evidence is not a demand forecast, customer count, or outcome validation. A qualifying payment-language statement is still only a text proxy: it is not a sale, conversion, or validated willingness-to-pay result. Every report includes validation next steps and identifies limitations.

## Evidence counts and scoring limits

- **Evidence records** are normalized source records, not unique customers, and may not be independent. Error/fallback records are excluded from analysis.
- **Sentence mentions** count qualifying occurrences in collected text. **Supporting evidence items** count distinct source records supporting an extracted problem-language pattern; several sentences in one record do not become several people or independent records.
- **Author/channel accounts** are counted only when a source exposes an identity (for example, Reddit author metadata or a YouTube channel ID/name). They are source-local account identifiers or names, not guaranteed unique people, and cannot be deduplicated reliably across platforms. Missing identities are reported as unavailable, not zero.
- A gap-theme count is the number of distinct eligible evidence items matching that theme, not the number of customers or proof that the gap is recurring.
- Generic words such as `buy`, `pay`, `cost`, `price`, `worth`, or a bare price do not raise the payment-language proxy. Only explicit first-person payment-intent or reported purchase/payment statements qualify; each evidence record counts at most once. The proxy is capped at **0.50** and accounts for **10%** of the composite score. A stated intention is not an actual purchase.
- The 0–100 **heuristic priority score** combines a problem-fit heuristic (40%), capped payment-language proxy (10%), competition-gap heuristic (20%), and evidence-record-volume coverage (30%). Coverage is a quantity input, not source quality. The score and its bands are not calibrated against real-world sales, demand, or customer outcomes and must not be interpreted as a probability or validated ranking.
- Starter/core/premium prices are product-pricing hypotheses, not scraped sales prices or observed willingness to pay. Direct pricing should be tested with interviews, paid pilots, or pre-sales.

Competition-gap themes are heuristic matches against eligible collected evidence, with the matched excerpt and source retained for review. The system never fills missing gap evidence with generic examples. If no recognized gap terms are present (or only collector fallback/error messages were returned), the report says **insufficient evidence** and assigns the competition-gap dimension a neutral `0.50`; that value is not an observed gap or positive gap credit. Marketplace listing presence, format, and estimated price are reported separately and are not treated as proof of a product gap.

Marketplace prices are shown only when a price is present in collected listing text. If scraping finds no price, the observed-price field is **Not found**; it is never filled with a default range. A separate **format benchmark estimate** may be shown for recognizable formats as a heuristic starting point. It is not an observed marketplace price and should be validated independently. Opportunity-level starter/core/premium amounts are also explicitly labeled as pricing hypotheses, not scraped listing prices.

## Run locally

```bash
cd digital-product-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/main.py
```

Open the local URL printed by Streamlit. The app is mobile-friendly and works without cloud credentials in local mode. For richer YouTube metadata, set `YOUTUBE_API_KEY` before starting the app. The application also accepts `REQUEST_TIMEOUT`, `MAX_ITEMS_PER_SOURCE`, and `USER_AGENT` environment variables.

## Deploy

For the private Streamlit Community Cloud + Supabase setup, follow [Private cloud deployment](#private-streamlit-community-cloud--supabase-deployment) below. For local Termux/PC use, keep `APP_STORAGE_BACKEND=local`; local data must remain on writable persistent storage. For a simple local server:

```bash
streamlit run app/main.py --server.address 0.0.0.0 --server.port 8501
```

## Project layout

The `app/collectors` package contains public-source collectors. `app/processors` cleans evidence and mines problem language. `app/analyzers` scores spending and competitor-gap signals. `app/product` generates and validates product-system concepts. `app/pdf` creates the ReportLab output. `app/database` defines Pydantic schemas and the SQLite/Postgres plus local/private-file storage abstractions. `app/ui` provides the Streamlit form and execution view.

## Data and operational limits

Public endpoints can rate-limit, change markup, or return incomplete results. The collectors retain failures as notes in the report rather than silently hiding them. Reddit and web collection use public HTTP endpoints and should be used respectfully. YouTube transcript extraction is not assumed from a public search page; use a compliant transcript provider or the YouTube API if transcript-level analysis is required. This V1 is a research prioritization tool, not legal, financial, or market-size advice.

## Suggested validation loop

Use the report to choose one opportunity. Interview five target users, show the smallest useful workflow, and ask for a paid pilot or pre-order. Track activation, completion, objections, and willingness to pay. Update the product only after comparing those direct signals with the report's public evidence.

## Private account backup

After signing in, use **Prepare private backup (.zip)** in the sidebar, then **Download your private backup (.zip)**, to download application source plus only your reports, products, version histories, QA runs, and visual files. The shared database, account/password hashes, session records, secrets, and other accounts are excluded. This per-account export is not a full database backup; see [Backup and recovery](#backup-and-recovery).


## AI Digital Product Factory — blueprint, content, design, and visuals

The Opportunity Portfolio offers **Generate Product** for each ranked opportunity. The Product Builder carries over the selected opportunity's audience, problem, promise, formats, differentiation, validation steps, and only the evidence records directly linked to its mined problem signal. Users can review/edit those inputs, choose among the 12 planned product types, generate a structured blueprint, edit it, and approve it. After approval, users can generate the blueprint's content one section at a time or explicitly request all remaining sections. Each section is a separate structured AI request, appears in the product preview, and is saved immediately so a later failure does not discard earlier sections. Users can edit cover title/subtitle, section names and purpose, block headings and text, examples, list items, and table cells. Each edit creates an account-scoped content version. A user can regenerate any saved section alone; this replaces that section while preserving other sections and saved cover copy. Drafts, blueprint versions, and content snapshots are stored in the configured database under the signed-in user's ID and can be reopened from the same opportunity card.

Blueprint generation is an on-demand call to an OpenAI-compatible chat-completions API. The default model is **GPT-5 mini**; set `PRODUCT_BUILDER_MODEL` to a compatible alternative if desired. Configure `PRODUCT_BUILDER_API_KEY` (or `OPENAI_API_KEY`) in the Streamlit server environment. For a compatible provider endpoint, set `PRODUCT_BUILDER_API_BASE`; otherwise the standard OpenAI endpoint is used. Do not put API keys in source code or commit them to the repository. The app requires `openai` from `requirements.txt`.

The provider receives only the selected opportunity fields and its linked evidence references, not the full research report. Blueprint and content generation happen only after the user initiates them. Content calls include the approved blueprint outline, the specific section being written, and the linked evidence references; they do not generate a whole book in one call. Regeneration makes one provider request for the selected section. Provider usage may incur charges under the configured provider's terms. If credentials, network access, or a compatible model are unavailable, the app shows an actionable error and preserves the research inputs and previously saved sections for retry; it does not silently substitute fabricated AI content.

The blueprint is explicitly labeled a **research-backed product concept** and **evidence-supported hypothesis worth validating**, not a guarantee of demand or sales. Evidence references are copied from the existing report; the AI is instructed not to invent sources, quotes, statistics, or outcomes. Generated citations are accepted only when their evidence IDs match references supplied from the selected report. A missing direct source link is flagged for validation. Each generated or materially edited blueprint is versioned in the configured database, and generated content snapshots are versioned as sections are saved and tied to the approved blueprint fingerprint. Existing databases are upgraded in place with the new content column and history table.

### Phase 4 — Template Engine

After at least one content section is saved, the Product Builder offers three reusable design profiles: **Minimal Professional** (editorial, restrained accent), **Modern Business** (action-led sans serif), and **Clean Workbook** (writing space and task markers). Each profile defines page size defaults, margins, typography hierarchy, accent, table, checkbox, callout, footer, and a reserved cover-image area. Structured blocks map to layout treatments such as checklists, tables, worksheets, examples, and action sequences. A deterministic format-based suggestion is shown but can be overridden. The chosen template and A4 / US Letter page size are saved on that product draft, scoped to the signed-in user; existing local SQLite product rows are upgraded in place with defaults.

The design-layout view is an in-app rendering of the existing content, not a generated image, complete print preview, or export. User-provided content is escaped before rendering; only HTTP(S) evidence URLs are linked. No additional provider call is made for design selection.

### Phase 5 — Content visuals

After at least one content section is saved, the Product Builder supports deterministic, content-supporting **icons**, simple **shapes**, and ordered **process diagrams**, plus user-uploaded **PNG/JPEG** images. Generated vector labels are escaped and use a small fixed accent palette. Diagrams are rendered from the user's ordered step labels (2–5 steps); they are not AI-inferred diagrams. Uploads are limited to 2 MB and 20 megapixels, decoded and re-encoded to strip metadata, and rejected if they are not valid PNG/JPEG images. Each product supports up to 12 visual assets, placed on its cover or a saved content section.

Visual bytes are stored as private local files in local mode or in a private Supabase Storage bucket in cloud mode; metadata and ownership remain in the configured database. All list, read, create, and remove operations are filtered by both the authenticated `user_id` and `product_id`; image files are not written to a public directory. Visuals appear in the indicative HTML design view and are included in the separate product PDF export when their saved placement is valid. This deterministic Phase 5 implementation makes no image-provider call and requires no image-generation API key; **AI image generation remains a later roadmap item**.

### Phase 6 — Preview and editing

The content workspace now includes a text-first editor for the product title and subtitle, each generated section's name and purpose, and its structured block headings, body/example text, list items, and table cells. Saves write a complete, validated content snapshot and increment the existing account-scoped content-version history. Editing section names does not rewrite the approved blueprint. Re-generating a section targets only that section, calls the existing configurable provider with the approved blueprint and linked evidence, validates citations against the supplied evidence IDs, and replaces only that section. Other saved sections and the custom cover copy remain unchanged. A failed provider call leaves the prior saved content available.

The in-app preview immediately reflects saved content edits and continues to show the selected design template, A4 / US Letter canvas, and the private Phase 5 visuals. The existing design controls cover template/page-size changes; existing visual controls let the user add, place, or remove supported visuals. This remains an indicative layout preview, not final print pagination.

### Current milestone scope and next steps

Completed through Phase 7 plus the requested product-PDF assembly/export milestone: selected opportunity → product page → research-derived editable inputs → product type choice and recommendation → structured blueprint generation → edit/approval/versioning → section-by-section content generation and evidence-ID validation → three reusable design templates, typed block-to-layout mapping, indicative A4 / US Letter preview, user-scoped template persistence and Phase 5 visuals → editable cover/section content, versioned saves, and single-section regeneration → deterministic content and preview QA with concrete findings and account-scoped run history → in-memory downloadable product PDF from the approved saved snapshot.

### Phase 7 — Automated content and preview QA

After at least one content snapshot is saved for an approved blueprint, select **Run automated QA** in the product workspace. Each run is tied to a fingerprint of the checked blueprint, content, selected design, private saved visuals, and deterministic preview HTML. Results are stored in the configured database only for the owning account and product. The view marks a result stale when any of those inputs changes. Earlier runs remain available in the same private product history.

The report has only **PASS** or **NEEDS REVISION** as its overall result, plus per-check **PASS**, **FLAG**, or **NOT RUN** states. It does not produce an arbitrary quality score. Available deterministic checks include:

- **Content:** missing/unexpected outline sections or title mismatches; empty sections; exact/high-text-similarity repeated blocks; narrowly matched explicit-negation sentence pairs; a short, disclosed list of grammar/formatting patterns; absolute/guarantee-style and specific numeric-claim candidates; a named list of generic filler phrases; and very short or named vague instruction patterns.
- **Product fit:** a small token-overlap prompt for researched-problem alignment and desired-outcome wording; presence of action-oriented block types; a small explicit product-type-to-component rule map; planned practical-material presence for formats that commonly need it; and whether source references were supplied.
- **Current HTML preview structure:** indicative cover-plus-section page containers and non-empty page text; heading count/order/text; rendered table count and row widths; whether saved visuals appear as embedded preview images with alt text; preview footer page labels; and selected template/page-size identifiers.

Every check shows its method and limitation alongside findings. These methods can miss problems or create false positives: lexical overlap is not semantic fit; similar text is not always unwanted; negation matching is not semantic contradiction detection; a citation ID is not proof; and component presence does not establish usability. Human review remains necessary for grammar, consistency, source support, clarity, and product usefulness. A **PASS** means only that these available checks raised no finding on that saved snapshot; it is not certification or a guarantee of accuracy, safety, usefulness, demand, sales, or success.

**PDF-specific checks remain explicitly NOT RUN by Phase 7 QA.** The PDF exporter performs a narrow structural read preflight (PDF opens, has pages, and includes extractable product-title text) and validates saved visual inputs before assembly. This is not a visual PDF review. Physical text overflow, final page breaks/empty pages, color, accessibility, reader-specific rendering, and visual correctness of embedded images are not automatically verified. Preview checks are not presented as PDF validation.

The QA history table is created/migrated in place in the configured database. QA reads only the authenticated owner's product and visual assets; saved run listing and writes are filtered by both `product_id` and `user_id`. QA adds no provider/API call, credential, or package dependency and does not modify product content or its content-version history.

### Product PDF assembly and export

The Product Builder now exposes **Download product PDF** after an approved blueprint and complete, saved content are present. Export re-reads the product using the signed-in `user_id` and `product_id`, rejects missing/foreign, unapproved, incomplete, schema-invalid, or blueprint-mismatched snapshots, then builds the PDF in memory. The product PDF is not written to a public/static directory or added to the opportunity-report exports. Each export uses the saved product title/subtitle/content, approved audience/type/desired outcome, account-scoped template and A4 / US Letter setting, and only that owner's saved visuals. Missing-section visual placements and unsupported/corrupt images fail with a user-correctable message.

The ReportLab document provides a cover, generated table of contents, section headings, paragraphs, numbered steps, checkboxes, tables with repeating headings, writing lines for worksheet/exercise/reflection blocks, cited reference blocks, page headers/footers, and page numbers. Saved PNG/JPEG uploads are decoded and dimension-checked again. Generated SVGs are restricted to the fixed static shape/text vocabulary before conversion; active/external SVG content is rejected. The implementation reuses the existing ReportLab stack and adds `svglib` (SVG conversion) and `pypdf` (structural read preflight) to `requirements.txt`.

The export view reports the latest saved QA status and whether it matches the current content/design/visual snapshot. A `NEEDS REVISION`, stale, or absent QA result is shown plainly but does not silently rewrite the product or prevent an informed user from exporting. Existing QA remains limited to deterministic content and indicative-preview checks; a QA `PASS` is not a clean-PDF certification. The structural PDF preflight does not detect physical text overflow or confirm that every reader/printer renders the file identically. The templates use ReportLab's built-in base fonts, so uncommon symbols or writing systems outside their glyph coverage need manual review. Review the downloaded PDF before distribution.

### Remaining milestones and deployment requirements

**Still deferred:** Phase 8 public-MVP deployment; print pagination and physical overflow inspection; AI image generation; advanced visual editing; and publishing integrations. The existing opportunity-scan PDF generator remains separate and is not reused as a product export path.

- No deployment or external project provisioning is performed by this repository change. Exact private Streamlit Community Cloud and Supabase steps are below. Product Builder API credentials remain separate optional secrets and are needed only when users request blueprint/content generation; PDF export makes no AI/provider call.
- In local mode, persist the writable `data/` directory (SQLite database plus `data/visual_assets/`). In cloud mode, reports and product records are in Postgres and visuals are private Storage objects; Community Cloud's local filesystem is not a durable cloud database.
- The in-app private export now includes the signed-in account's product content, QA history, and visual bytes as well as reports, but deliberately excludes shared account/session records. Also maintain an operator-level database dump; Postgres database dumps do not include Storage object bytes.
- `reportlab`, `svglib`, `pypdf`, Pillow, and the pure-Python `pg8000` driver are installed from `requirements.txt`. Deployment itself, secret provisioning, bucket creation, and backup verification remain operator tasks and were not performed here.

Run focused builder tests with `python product_factory_test.py`, `python product_template_test.py`, `python product_editing_test.py`, `python product_visual_test.py`, `python product_qa_test.py`, and `python product_pdf_test.py`. The V2.4–V2.7 regression checks remain available as `python v24_smoke_test.py` through `python v27_evidence_scoring_test.py`.


## Storage backends and strict mode selection

The app now has working database and file-storage abstractions; this is not a configuration-only switch.

- **Local (Termux/PC):** with no Supabase settings, the app uses SQLite at `data/opportunities.db` and stores visual bytes under `data/visual_assets/`. To force this mode, set `APP_STORAGE_BACKEND=local`. Keep both paths on writable persistent storage. This mode needs no Supabase project or cloud credentials.
- **Cloud:** set all three server-only values `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_DB_URL` in Streamlit secrets or environment variables. `APP_STORAGE_BACKEND=supabase` is recommended. The Postgres URI must use TLS; a Supabase Session Pooler URI is appropriate when direct connections are unavailable. The app writes relational app data to Postgres and object bytes to the named **private** Storage bucket.
- If no cloud values are present, the app selects local mode. If only some cloud values are present, or a requested cloud setting is malformed, startup fails closed. Once cloud mode is selected, database or Storage failures are shown as failures; the app never copies the operation to SQLite/local files. The database driver is pure Python (`pg8000`) to keep local Termux installation practical.
- `SUPABASE_STORAGE_BUCKET` is optional and defaults to `digital-product-assets`. The existing 2 MB visual validation limit and 12-assets-per-product limit remain. No public object URL or signed URL is made; visual downloads are streamed server-side from Supabase's authenticated-object route.

See [`secrets.example.toml`](secrets.example.toml) for placeholder-only configuration. Never commit populated secrets. A service-role key bypasses RLS and must stay only on the trusted Streamlit server.

### Supabase project setup and exact access controls

1. Create a Supabase project yourself. This project change does not create one or access external credentials.
2. Open **SQL Editor** and run [`supabase/schema.sql`](supabase/schema.sql). It creates/migrates the app tables and indexes in Postgres-compatible form. Startup also applies `ENABLE ROW LEVEL SECURITY`, revokes direct access from `anon`/`authenticated`, and grants the trusted `service_role` for the app tables.
3. In **Storage → New bucket**, create a bucket named `digital-product-assets` (or use the same name as `SUPABASE_STORAGE_BUCKET`), with **Public bucket OFF**, a 2 MB maximum, and allowed MIME types `image/png`, `image/jpeg`, `image/svg+xml`. Do not add public access policies. The server uses the service-role key for storage operations, which bypasses Storage RLS; therefore the app's account/product ownership checks are mandatory. Never use this key in browser code or client-side HTML.
4. The current username/password accounts are application-managed, not Supabase Auth identities. The app includes `user_id` in every report/product/asset/QA query and verifies product ownership before reading, writing, or removing assets. RLS is enabled as a second perimeter that denies direct browser-role access; it does **not** perform per-app-user filtering for the server's service-role connection. Do not grant database or Storage access directly to users. If you later move user access into Supabase Auth or browser clients, redesign policies to use `auth.uid()` and migrate identities before enabling client access.
5. Copy the project's HTTPS URL, the server-side service-role key, and a migration-capable Postgres connection URI from the Supabase dashboard. The URI must be able to create/alter the app tables and set RLS/grants (normally the project `postgres` owner through its Session Pooler). Use the supplied DB password and `sslmode=require`. Treat this database URI as a highly privileged secret, separate from the Storage service-role key. Do not use the publishable/anon key in place of the service-role key.

The `schema.sql` file is the exact RLS baseline for this app: RLS on all eight application tables; no `anon` or `authenticated` policies/grants; only the server role is granted access. The Storage bucket is private and has no browser-role policies. Supabase's service keys bypass these policies by design, so the service credential and owner-filtered application code are part of the trust boundary.

### Private Streamlit Community Cloud + Supabase deployment

1. Put this project in a **private GitHub repository**. In Streamlit Community Cloud, create an app from that repository, set the entry point to `app/main.py`, and use the repository's `requirements.txt`.
2. In the Streamlit app settings, set app visibility/access to **Private** and grant access only to intended viewers. Do not embed the service-role key in source, UI output, a URL, or a committed file.
3. In **App settings → Secrets**, copy the values and names from [`secrets.example.toml`](secrets.example.toml), replacing every placeholder. Keep `APP_STORAGE_BACKEND="supabase"`. Secrets may alternatively be environment variables for other supported hosts. `SUPABASE_DB_URL` is itself sensitive because it includes a database password.
4. Confirm the database schema and private bucket configuration above. Restart/redeploy the Streamlit app. Sign in or create an application account, then create a scan and a product; check that reports/product tables persist in Postgres and uploaded visuals appear as objects in the private bucket. A restore/read/write failure should remain visible rather than falling back locally.
5. Configure the existing AI provider key separately if Product Builder generation is needed. No AI key is required for Postgres, Storage, research/PDF export, and no credentials are included in the example file.

The app's authentication uses the existing account model and session cookie. Streamlit Community Cloud hosts an ephemeral filesystem, so cloud mode does not depend on its SQLite or `data/` files for application records. Opportunity-scan PDFs written in the local `data/reports/` directory may be ephemeral on Community Cloud; the report data and per-account JSON export persist in Supabase.

### Database TLS modes (`sslmode`) and storage-fix notes

`SUPABASE_DB_URL` accepts these libpq-style TLS modes; anything else (`disable`, `allow`, `prefer`, unknown values) is rejected at startup because it could downgrade to plaintext:

- `sslmode=require` (default when omitted): connection is encrypted, but the server certificate is **not verified** (same as libpq). This works out of the box because Supabase database certificates are signed by Supabase's own CA, which public trust stores do not contain.
- `sslmode=verify-ca` / `sslmode=verify-full`: verify the certificate chain (and hostname for `verify-full`). Download the CA certificate from the Supabase dashboard (Database settings, SSL configuration) and reference it as `...?sslmode=verify-full&sslrootcert=/path/to/supabase-ca.crt`. This is the stronger option because it also protects against a man-in-the-middle.

Connection failures report the driver's exception class (never the password, host, or driver message) and a TLS hint when relevant; the full URL is never logged.

Operational notes: schema/RLS setup runs once per server process, and the store objects are cached across Streamlit reruns. If a Storage object cannot be removed after its database row is deleted (for example during an outage), the asset disappears from the app, a warning naming the orphaned object key is logged, and the object needs manual cleanup in the bucket (keys are `user_id/product_id/asset_id.ext`). `ReportStore.save` refuses to overwrite or re-assign a report id that belongs to another account.

### SQLite-to-Supabase migration

Migration is opt-in, idempotent, and never runs automatically:

1. Stop writes to the local app. Make a protected copy of `data/opportunities.db` and `data/visual_assets/` first. Keep the backup private because the DB includes password hashes and session hashes.
2. Install the app's `requirements.txt` in a Python environment with access to the source directory. Set the complete cloud secrets (or Streamlit-compatible environment variables) and `APP_STORAGE_BACKEND=supabase`; do **not** point normal app operation back to local mode.
3. Review a local-only dry-run (does not connect to Supabase):
   ```bash
   python scripts/migrate_sqlite_to_supabase.py --sqlite data/opportunities.db
   ```
4. After creating the private bucket and running the schema, execute the import:
   ```bash
   python scripts/migrate_sqlite_to_supabase.py --sqlite data/opportunities.db --execute
   ```
   It copies application users/password hashes and sessions, reports, products, blueprint/content versions, QA runs, and visuals (from legacy SQLite BLOBs or `data/visual_assets/`). Existing primary keys are not overwritten, so a failed import can be rerun. The script prints inserted row counts and never prints secret values. Keep the source backup until you have tested sign-in, report history, product previews, visual display, and exports on the cloud app. The migration imports sessions too; if you prefer to invalidate browser sessions, delete migrated rows from `public.sessions` after import so users log in again.
5. Do not run `--execute` twice against a different source if IDs overlap: existing cloud rows are intentionally left unchanged. Take a fresh cloud backup before any manual reconciliation.

### Backup and recovery

Supabase Free projects may be paused after a week of low activity, and the current Supabase documentation says paused projects can be restored for up to one year. **Free does not include automatic daily database backups.** Supabase recommends regular exports and off-site copies. Storage object bytes are not included in database backups.

- **Per-account application export:** each signed-in user clicks **Prepare private backup (.zip)** and then **Download your private backup (.zip)** in the sidebar. The archive is built only on request (not on every page rerun). It includes that account's reports, products, version histories, QA runs, visuals, and source; it excludes credentials, sessions, the shared DB, and all other accounts. Save it encrypted/offline. For a multi-account deployment, each account must export its own account data through the app.
- **Operator Postgres backup:** install PostgreSQL client tools, prompt for the database URI without saving it in shell history, and dump the app's `public` schema regularly to encrypted off-site storage. Example:
  ```bash
  read -rsp 'Supabase Postgres URI: ' SUPABASE_DB_URL; echo
  pg_dump --dbname="$SUPABASE_DB_URL" --schema=public --format=custom --file="dpe-public-$(date +%F).dump"
  unset SUPABASE_DB_URL
  ```
  A database dump does **not** back up visual object bytes. Preserve each account's ZIP export as well, or separately mirror the private bucket using Supabase Storage's S3-compatible interface and dedicated Storage S3 credentials. Test a restore into a separate project before relying on the procedure.
- For local mode, stop the app and copy both `data/opportunities.db` and `data/visual_assets/` together to encrypted/off-site storage. The app ZIP export is an additional owner-scoped option, not a replacement for a full local backup.

Official references: [Supabase Free project pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [database backups](https://supabase.com/docs/guides/platform/backups), [private object downloads](https://supabase.com/docs/guides/storage/serving/downloads), [Storage access control](https://supabase.com/docs/guides/storage/security/access-control), and [object deletion](https://supabase.com/docs/guides/storage/management/delete-objects). Verify current plan limits and restore/retention policies in Supabase before making operational decisions.

### Storage limitations and security notes

- The current app remains a single trusted Streamlit server using a service-role Storage credential and a database connection URI; Supabase RLS does not authenticate these custom app users individually. Do not deploy with either credential exposed or create client-side database access.
- Local filesystem writes and SQLite are suitable for one local Termux/PC instance, not multiple cloud replicas. Use Supabase cloud mode for shared durable records.
- Remote bucket creation is manual. A missing bucket, TLS/database issue, bad credential, or quota/rate error is surfaced and is not converted into local writes.
- This task implements code, docs, and tests only. No Supabase project/bucket was created, no data was migrated, and the Streamlit app was not deployed.

## Product format coverage

The free-first build includes deterministic, type-specific blueprint profiles and template recommendations for all supported product formats:

- Ebook
- Playbook
- Workbook
- Planner
- Checklist
- Guide
- Journal
- Tracker
- Action Plan
- Challenge
- Template
- Worksheet

Each format keeps the existing three visual design systems while receiving purpose-built section structure and content-block recommendations. This keeps the renderer stable while making the product factory useful across multiple digital-product formats before deployment.
