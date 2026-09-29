-- Run in Supabase SQL Editor before first cloud launch (or let the app's idempotent
-- schema initializer create these tables). App accounts are the project's existing
-- username/password accounts; they are not Supabase Auth accounts.
CREATE TABLE IF NOT EXISTS public.users (
  user_id text PRIMARY KEY,
  username text UNIQUE NOT NULL,
  password_hash text NOT NULL,
  created_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.sessions (
  token_hash text PRIMARY KEY,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  expires_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.reports (
  id text PRIMARY KEY,
  topic text NOT NULL,
  created_at text NOT NULL,
  payload text NOT NULL,
  user_id text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS reports_user_created ON public.reports(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.products (
  product_id text PRIMARY KEY,
  user_id text NOT NULL,
  source_report_id text NOT NULL,
  opportunity_index integer NOT NULL,
  opportunity_name text NOT NULL,
  source_payload text NOT NULL,
  inputs_payload text NOT NULL,
  blueprint_payload text,
  status text NOT NULL DEFAULT 'draft',
  created_at text NOT NULL,
  updated_at text NOT NULL,
  content_payload text,
  design_template_id text NOT NULL DEFAULT 'minimal_professional',
  page_size text NOT NULL DEFAULT 'letter'
);
CREATE INDEX IF NOT EXISTS products_user_updated ON public.products(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS public.product_versions (
  version_id text PRIMARY KEY,
  product_id text NOT NULL REFERENCES public.products(product_id) ON DELETE CASCADE,
  version_number integer NOT NULL,
  blueprint_payload text NOT NULL,
  change_summary text NOT NULL,
  created_at text NOT NULL,
  UNIQUE(product_id, version_number)
);
CREATE TABLE IF NOT EXISTS public.product_content_versions (
  version_id text PRIMARY KEY,
  product_id text NOT NULL REFERENCES public.products(product_id) ON DELETE CASCADE,
  version_number integer NOT NULL,
  content_payload text NOT NULL,
  change_summary text NOT NULL,
  created_at text NOT NULL,
  UNIQUE(product_id, version_number)
);
CREATE TABLE IF NOT EXISTS public.product_assets (
  asset_id text PRIMARY KEY,
  product_id text NOT NULL REFERENCES public.products(product_id) ON DELETE CASCADE,
  user_id text NOT NULL,
  asset_type text NOT NULL,
  title text NOT NULL,
  filename text NOT NULL,
  mime_type text NOT NULL,
  placement text NOT NULL,
  metadata_json text NOT NULL,
  content bytea NOT NULL DEFAULT ''::bytea,
  created_at text NOT NULL,
  storage_path text
);
CREATE INDEX IF NOT EXISTS product_assets_owner ON public.product_assets(user_id, product_id, created_at);
CREATE TABLE IF NOT EXISTS public.product_qa_runs (
  run_id text PRIMARY KEY,
  product_id text NOT NULL REFERENCES public.products(product_id) ON DELETE CASCADE,
  user_id text NOT NULL,
  snapshot_fingerprint text NOT NULL,
  result_payload text NOT NULL,
  created_at text NOT NULL
);
CREATE INDEX IF NOT EXISTS product_qa_owner ON public.product_qa_runs(user_id, product_id, created_at DESC);

-- Important: never expose the service-role key to a browser/client. RLS is enabled,
-- but this server-side app uses the service role and therefore bypasses RLS; the app
-- enforces custom-account ownership in each query. Deny all direct client access.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_content_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_qa_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.users, public.sessions, public.reports, public.products,
  public.product_versions, public.product_content_versions, public.product_assets,
  public.product_qa_runs FROM anon, authenticated;
GRANT ALL ON public.users, public.sessions, public.reports, public.products,
  public.product_versions, public.product_content_versions, public.product_assets,
  public.product_qa_runs TO service_role;

-- Storage bucket must be private. No public policy is used by this application.
-- Create it in Dashboard -> Storage -> New bucket:
--   name: digital-product-assets (or match SUPABASE_STORAGE_BUCKET)
--   public: OFF
--   file size limit: 2 MB (or stricter)
--   allowed MIME: image/png,image/jpeg,image/svg+xml
-- The app reads/writes through server-side Storage API using service-role credentials;
-- no public URL or signed URL is generated. Do not grant anon/authenticated access to
-- storage.objects for this bucket. service_role bypasses Storage RLS; app ownership
-- checks on product_id + user_id are mandatory.
