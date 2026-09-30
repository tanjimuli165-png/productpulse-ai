import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import extra_streamlit_components as stx
import streamlit as st

from app.analyzers.gap_detector import detect_gaps
from app.analyzers.spending import analyze_spending
from app.collectors.reddit import RedditCollector
from app.collectors.web import WebCollector
from app.collectors.youtube import YouTubeCollector
from app.collectors.marketplaces import MarketplaceCollector
from app.collectors.site_search import InstagramCollector, QuoraCollector
from app.config import BASE_DIR, MAX_ITEMS_PER_SOURCE, REPORT_DIR
from app.backup import create_user_backup
from app.database.backend import StorageConfigurationError, StorageConnectionError, load_storage_config
from app.database.file_storage import StorageOperationError
from app.database.db import ReportStore
from app.database.models import Evidence, Opportunity, ProblemSignal, Report
from app.product.storage import ProductStore
from app.ui.product_generator import render_product_generator
from app.evidence_metrics import count_identified_contributors, is_eligible_evidence
from app.pdf.generator import generate_pdf
from app.product.architect import architect_opportunities
from app.product.launch_suite import build_blueprint, build_launch_kit
from app.processors.cleaner import normalize_evidence
from app.processors.problem_miner import mine_problems
from app.processors.objection_framework import build_objection_matrix
from app.search_utils import normalize_query


QUICK_TOPICS = {"Meal Prep": "meal prep", "SaaS Ideas": "SaaS ideas", "Parenting": "parenting"}
AUTH_COOKIE = "dpe_auth_session"


def _set_example(topic: str) -> None:
    st.session_state["topic_input"] = topic


def _restore_cookie_session(store: ReportStore, cookies: stx.CookieManager) -> None:
    """Restore the server-side session after a browser refresh/reconnect."""
    if st.session_state.get("auth_user"):
        return

    # On a real browser refresh, Streamlit's request context contains the
    # cookies sent with the initial request. Treat that source as authoritative
    # so an asynchronous CookieManager component cannot race the login view.
    context_available = False
    token = None
    try:
        context_cookies = st.context.cookies
        context_available = True
        token = context_cookies.get(AUTH_COOKIE)
    except (AttributeError, RuntimeError):
        pass

    if not context_available:
        try:
            token = cookies.get_all(key="restore_auth_cookie").get(AUTH_COOKIE)
        except Exception:
            token = None

    if token:
        user = store.authenticate_session(token)
        if user:
            st.session_state["auth_user"] = user
            st.session_state["session_token"] = token


def _restore_builder_state_from_url() -> None:
    """Rebuild the product page state after a full browser refresh."""
    if st.session_state.get("product_builder_state"):
        return
    try:
        params = st.query_params
        report_id = params.get("product_report_id")
        opportunity_index = params.get("product_opportunity_index")
        product_id = params.get("product_id")
        if report_id and product_id and opportunity_index is not None:
            st.session_state["product_builder_state"] = {
                "report_id": report_id,
                "opportunity_index": int(opportunity_index),
                "product_id": product_id,
            }
    except (AttributeError, ValueError, TypeError):
        pass


def _persist_builder_state(report_id: str, opportunity_index: int, product_id: str) -> None:
    state = {
        "report_id": report_id,
        "opportunity_index": int(opportunity_index),
        "product_id": product_id,
    }
    st.session_state["product_builder_state"] = state
    try:
        st.query_params.update(
            product_report_id=report_id,
            product_opportunity_index=str(opportunity_index),
            product_id=product_id,
        )
    except Exception:
        pass


def _clear_builder_state() -> None:
    st.session_state.pop("product_builder_state", None)
    try:
        for key in ("product_report_id", "product_opportunity_index", "product_id"):
            st.query_params.pop(key, None)
    except Exception:
        pass


def _render_auth(store: ReportStore, cookies: stx.CookieManager) -> str | None:
    if st.session_state.get("auth_user"):
        user = st.session_state["auth_user"]
        st.sidebar.subheader("Account")
        st.sidebar.success(f"Signed in as **{user['username']}**")
        if st.sidebar.button("Log out", key="logout_button"):
            token = st.session_state.get("session_token", "")
            store.revoke_session(token)
            st.session_state.pop("user_backup", None)
            cookies.delete(AUTH_COOKIE, key="delete_auth_cookie")
            st.session_state.pop("auth_user", None)
            st.session_state.pop("session_token", None)
            st.session_state.pop("report", None)
            st.session_state.pop("pdf_path", None)
            st.rerun()
        return user["user_id"]

    st.subheader("Welcome — sign in to begin")
    st.caption("Your scans and products are stored in the configured private account storage.")
    login_tab, register_tab = st.tabs(["Log in", "Register"])
    with login_tab:
        with st.form("main_login_form"):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            user = store.authenticate(username, password)
            if user:
                token = store.create_session(user["user_id"], days=30)
                cookies.set(AUTH_COOKIE, token, key="set_auth_cookie", expires_at=datetime.now(timezone.utc) + timedelta(days=30), max_age=30 * 24 * 60 * 60, path="/", secure=True, same_site="lax")
                st.session_state["auth_user"] = user
                st.session_state["session_token"] = token
                # Do not immediately rerun here. CookieManager writes through an
                # async browser component; continuing this run lets the browser
                # receive the persistent cookie before any refresh can occur.
                return user["user_id"]
            st.error("Invalid username or password.")
    with register_tab:
        with st.form("main_register_form"):
            new_username = st.text_input("New username", key="register_username")
            new_password = st.text_input("Password (8+ characters)", type="password", key="register_password")
            confirm_password = st.text_input("Confirm password", type="password", key="register_confirm")
            registered = st.form_submit_button("Create account")
        if registered:
            if new_password != confirm_password:
                st.error("Passwords do not match.")
            else:
                ok, message = store.register_user(new_username, new_password)
                (st.success if ok else st.error)(message)
        st.info("Log in or create an account to run scans and access private history.")
    return None




def _extract_reference_material(uploaded_files) -> str:
    """Read small user-supplied text/PDF references without requiring a separate service."""
    chunks: list[str] = []
    for uploaded in uploaded_files or []:
        name = str(getattr(uploaded, "name", "reference"))
        suffix = Path(name).suffix.lower()
        try:
            raw = uploaded.getvalue()
            if suffix in {".txt", ".md", ".markdown", ".csv"}:
                text = raw.decode("utf-8", errors="replace")
            elif suffix == ".pdf":
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw))
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
            else:
                text = ""
            text = text.strip()
            if text:
                chunks.append(f"[{name}]\n{text[:12000]}")
        except Exception as exc:
            chunks.append(f"[{name}]\n[Could not extract text: {type(exc).__name__}]")
    return "\n\n".join(chunks)[:50000]


def _build_manual_topic_report(topic: str, description: str, audience: str, desired_outcome: str, materials: str, user_id: str) -> Report:
    """Create a source-labeled report for creator-supplied topics without pretending it is market research."""
    topic = topic.strip()
    description = description.strip()
    audience = audience.strip() or "People interested in this topic"
    desired_outcome = desired_outcome.strip() or f"A practical resource that helps the reader work with {topic}."
    evidence = []
    if materials.strip():
        evidence.append(Evidence(
            source="User provided",
            title="Creator-supplied reference material",
            text=materials.strip(),
            metadata={"origin": "manual_topic_input"},
        ))
    problem = ProblemSignal(
        problem=description,
        customer_language=[description],
        frequency=1,
        sentence_mentions=1,
        evidence_items=len(evidence),
        unique_contributors=None,
        evidence_urls=[],
        urgency=0.0,
        objection_bucket="Creator-supplied topic",
    )
    opportunity = Opportunity(
        name=f"{topic} Digital Product",
        audience=audience,
        promise=desired_outcome,
        format=["Guide", "Workbook", "Playbook"],
        problem_fit=0.0,
        willingness_to_pay=0.0,
        competition_gap=0.0,
        competition_gap_status="not_assessed",
        evidence_strength=0.0,
        validation_score=0.0,
        pricing={"benchmark": "Not assessed"},
        pricing_rationale="No marketplace or payment research was run for this creator-supplied topic.",
        value_hook="Creator-supplied topic and materials will be used as the starting brief.",
        objection_bucket="Creator-supplied topic",
        components=["Topic-specific explanation", "Actionable steps", "Exercises or worksheets where appropriate"],
        differentiation=["Uses the creator's supplied description and reference material"],
        risks=["Topic fit and demand were not independently researched in this mode."],
        next_steps=["Review the generated blueprint", "Check all factual claims against supplied materials", "Validate the finished product with intended readers"],
    )
    return Report(
        id=uuid.uuid4().hex,
        topic=topic,
        user_id=user_id,
        scoring_version="manual_topic_v1",
        evidence=evidence,
        problems=[problem],
        opportunities=[opportunity],
        executive_summary="Creator-supplied topic brief. This report is not a market-research result.",
        collection_notes=["Created from the user's topic, description, and optional supplied reference material; no public-source demand claim is made."],
    )

def _build_research_audit(
    reportable_evidence: list[Evidence],
    problems: list[ProblemSignal],
    marketplace_gaps: list,
    gap_analysis: dict,
) -> dict:
    """Create a transparent research-readiness audit without inventing a demand score."""
    source_names = sorted({item.source for item in reportable_evidence if item.source})
    contributors = count_identified_contributors(reportable_evidence)
    corroborated_problems = [
        problem for problem in problems
        if (problem.evidence_items or 0) >= 2
    ]
    cross_source_problems = []
    for problem in problems:
        evidence_urls = set(problem.evidence_urls)
        matched_sources = {
            item.source
            for item in reportable_evidence
            if item.url and item.url in evidence_urls and item.source
        }
        if len(matched_sources) >= 2:
            cross_source_problems.append(problem)
    checks = [
        {
            "name": "Eligible evidence records",
            "status": "PASS" if len(reportable_evidence) >= 5 else "REVIEW",
            "message": f"{len(reportable_evidence)} eligible normalized source records remain after cleaning and deduplication.",
            "limitation": "Record count is coverage, not source quality, independence, or customer count.",
        },
        {
            "name": "Source diversity",
            "status": "PASS" if len(source_names) >= 3 else "REVIEW",
            "message": f"Evidence spans {len(source_names)} source type(s): {', '.join(source_names) or 'none'}.",
            "limitation": "Different source types can still repeat the same underlying information.",
        },
        {
            "name": "Explicit problem signals",
            "status": "PASS" if problems else "FLAG",
            "message": f"{len(problems)} problem-language group(s) were extracted from eligible evidence.",
            "limitation": "Extracted language is heuristic and does not prove recurrence among people.",
        },
        {
            "name": "Problem corroboration across evidence records",
            "status": "PASS" if corroborated_problems else "REVIEW",
            "message": (
                f"{len(corroborated_problems)} problem-language group(s) are supported by at least two distinct evidence records."
                if corroborated_problems
                else "No problem-language group currently has two or more distinct supporting evidence records."
            ),
            "limitation": "Distinct source records are not proof of distinct people, recurrence over time, or causal importance.",
        },
        {
            "name": "Problem corroboration across source types",
            "status": "PASS" if cross_source_problems else "REVIEW",
            "message": (
                f"{len(cross_source_problems)} problem-language group(s) have evidence from at least two source types."
                if cross_source_problems
                else "No extracted problem-language group is currently linked to evidence from two or more source types."
            ),
            "limitation": "Source-type diversity improves triangulation but can still reflect the same underlying conversation or copied information.",
        },
        {
            "name": "Source-local contributor identifiers",
            "status": "PASS" if contributors is not None and contributors >= 3 else "REVIEW",
            "message": (
                f"{contributors} source-local contributor identifiers were available."
                if contributors is not None
                else "No source-local contributor identifiers were available."
            ),
            "limitation": "These identifiers are source-local and are not a cross-platform unique-person count.",
        },
        {
            "name": "Competition-gap evidence",
            "status": "PASS" if gap_analysis.get("status") == "observed" and gap_analysis.get("gaps") else "REVIEW",
            "message": (
                f"{len(gap_analysis.get('gaps', []))} observed gap theme(s) were matched to distinct evidence records."
                if gap_analysis.get("status") == "observed"
                else "No recognized competition-gap theme was verified from collected evidence."
            ),
            "limitation": "A matched gap term does not prove an unmet market need.",
        },
        {
            "name": "Comparable marketplace listings",
            "status": "PASS" if marketplace_gaps else "REVIEW",
            "message": f"{len(marketplace_gaps)} marketplace listing record(s) are available for side-by-side review.",
            "limitation": "Listing presence, price, ratings, and review snippets are not proof of demand or sales.",
        },
    ]
    core_research_ready = bool(
        len(reportable_evidence) >= 5
        and len(source_names) >= 2
        and problems
        and corroborated_problems
        and (marketplace_gaps or (gap_analysis.get("status") == "observed" and gap_analysis.get("gaps")))
    )
    return {
        "research_status": (
            "Sufficient research basis for product exploration"
            if core_research_ready
            else "More evidence needed before treating this opportunity as research-ready"
        ),
        "checks": checks,
        "source_types": source_names,
        "eligible_evidence_count": len(reportable_evidence),
        "problem_group_count": len(problems),
        "marketplace_listing_count": len(marketplace_gaps),
        "next_validation": [
            "Review the original source records behind the strongest problem signals.",
            "Compare the proposed product directly with observed alternatives before making differentiation or positioning claims.",
            "Test the problem and product with intended readers; research evidence alone does not validate willingness to pay or product-market fit.",
        ],
    }


def run_engine(topic: str, sources: list[str], limit: int) -> Report:
    topic = normalize_query(topic)
    collectors = {"Reddit": RedditCollector(), "YouTube": YouTubeCollector(), "Web": WebCollector(), "Quora": QuoraCollector(), "Instagram": InstagramCollector()}
    evidence, notes = [], []
    with ThreadPoolExecutor(max_workers=len(sources) + 1) as pool:
        futures = {pool.submit(collectors[s].collect, topic, limit): s for s in sources}
        marketplace_future = pool.submit(MarketplaceCollector().collect, topic, min(limit, 10))
        for future in as_completed(futures):
            source = futures[future]
            try:
                evidence.extend(future.result())
            except Exception as exc:
                notes.append(f"{source} failed: {exc}")
        try:
            marketplace_gaps = marketplace_future.result()
        except Exception as exc:
            marketplace_gaps = []
            notes.append(f"Marketplace collection failed: {exc}")
    evidence = normalize_evidence(evidence)
    reportable_evidence = [item for item in evidence if is_eligible_evidence(item)]
    problems = mine_problems(evidence)
    objection_matrix = build_objection_matrix(problems)
    spending = analyze_spending(evidence)
    gaps = detect_gaps(evidence)
    opportunities = architect_opportunities(topic, problems, spending, gaps, len(reportable_evidence), marketplace_gaps=marketplace_gaps)
    top_opportunity = opportunities[0] if opportunities else None
    top_problem = problems[0] if problems else None
    product_blueprint = build_blueprint(topic, top_opportunity, top_problem) if top_opportunity else {}
    launch_kit = build_launch_kit(topic, top_opportunity, top_problem, marketplace_gaps) if top_opportunity else {}
    research_audit = _build_research_audit(reportable_evidence, problems, marketplace_gaps, gaps)
    best = opportunities[0].validation_score if opportunities else 0
    contributors = count_identified_contributors(reportable_evidence)
    contributor_note = f" The records exposed {contributors} distinct source-local author/channel account identifiers or names; this is not necessarily a count of unique people." if contributors is not None else " Author/channel identities were not available in the eligible records."
    summary = (f"The scan found {len(reportable_evidence)} eligible normalized evidence records and {len(problems)} extracted problem-language groups across {', '.join(sources)}. "
               f"The highest heuristic priority score was {best}/100. Groups are text patterns, not unique people or proof of recurrence.{contributor_note} "
               "Scores are ranking heuristics, not validated demand or willingness to pay; interviews, a paid pilot, or a pre-sale are needed to test those outcomes.")
    sales_hooks = [opportunity.value_hook for opportunity in opportunities if opportunity.value_hook]
    return Report(
        id=uuid.uuid4().hex,
        topic=topic,
        evidence=evidence,
        problems=problems,
        opportunities=opportunities,
        objection_matrix=objection_matrix,
        sales_hooks=sales_hooks,
        marketplace_gaps=marketplace_gaps,
        gap_analysis=gaps,
        product_blueprint=product_blueprint,
        launch_kit=launch_kit,
        research_audit=research_audit,
        executive_summary=summary,
        scoring_version="evidence_proxy_v2",
        collection_notes=notes + [spending["interpretation"], gaps["assessment"], "The composite priority score is a heuristic, not an outcome-validated demand score or probability."],
    )


def _render_source_links(report: Report) -> None:
    evidence = [item for item in report.evidence if is_eligible_evidence(item)]
    sources = sorted({item.source for item in evidence})
    for source in sources:
        with st.expander(f"{source} source links ({sum(item.source == source for item in evidence)} evidence records)"):
            for item in [item for item in evidence if item.source == source]:
                label = item.title or item.url or "Open source"
                st.markdown(f"- [{label}]({item.url})" if item.url else f"- {label}")


def _load_history_report(report: Report) -> None:
    pdf_path = REPORT_DIR / f"opportunity-report-{report.id}.pdf"
    if not pdf_path.exists():
        generate_pdf(report, pdf_path)
    st.session_state["report"] = report
    st.session_state["pdf_path"] = str(pdf_path)


def _render_history(store: ReportStore, user_id: str) -> None:
    st.sidebar.subheader("Past Opportunity Scans")
    history = store.recent(user_id=user_id, limit=12)
    if not history:
        st.sidebar.caption("Your completed scans will appear here.")
        return
    for saved_report in history:
        timestamp = saved_report.created_at.strftime("%Y-%m-%d %H:%M")
        label = saved_report.topic[:42] + ("…" if len(saved_report.topic) > 42 else "")
        if st.sidebar.button(label, key=f"history_{saved_report.id}", help=f"Load scan from {timestamp}"):
            _load_history_report(saved_report)
        st.sidebar.caption(f"{timestamp} · {len(saved_report.problems)} problem-language groups")


@st.cache_resource(show_spinner=False)
def _get_stores() -> tuple[ReportStore, ProductStore]:
    """One store pair per server process, so cloud schema/RLS setup never runs on each rerun."""
    return ReportStore(), ProductStore()


def _render_backup_controls(store: ReportStore, product_store: ProductStore, user_id: str) -> None:
    """Build the private ZIP only on request: it reads all of the account's data and visual files."""
    if st.sidebar.button(
        "Prepare private backup (.zip)",
        key="prepare_user_backup",
        help="Builds an archive with app source and only your reports, product versions, QA history, and private visuals. It excludes the shared database, credentials, sessions, and other accounts.",
    ):
        try:
            with st.spinner("Building your private backup…"):
                st.session_state["user_backup"] = {
                    "user_id": user_id,
                    "data": create_user_backup(BASE_DIR, store, user_id, product_store),
                }
        except StorageOperationError:
            st.session_state.pop("user_backup", None)
            st.sidebar.error("Private storage could not be read right now, so no backup was created. Please try again shortly.")
    backup = st.session_state.get("user_backup")
    if backup and backup.get("user_id") == user_id:
        st.sidebar.download_button(
            "Download your private backup (.zip)",
            data=backup["data"],
            file_name="digital-product-engine-my-backup.zip",
            mime="application/zip",
            key="user_project_backup",
        )


def render():
    st.set_page_config(page_title="ProductPulse AI", page_icon="◈", layout="wide")
    st.markdown("""
<style>
:root{--pp-ink:#0F172A;--pp-navy:#134E4A;--pp-text:#334155;--pp-muted:#64748B;--pp-accent:#0F766E;--pp-soft:#E6F2F0;--pp-bg:#F8FAFC;--pp-border:#D8E2E0;--pp-shadow:0 10px 28px rgba(15,23,42,.055)}
.block-container{max-width:1180px;padding-top:1.1rem;padding-bottom:5rem}
[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 82% 0%,rgba(15,118,110,.07),transparent 30rem),#F8FAFC}
[data-testid="stHeader"]{background:rgba(248,250,252,.86);backdrop-filter:blur(14px)}
h1,h2,h3{color:var(--pp-ink);letter-spacing:-.035em}
.stCaption,[data-testid="stCaptionContainer"]{color:var(--pp-muted)}
label{color:var(--pp-text)!important;font-weight:650!important}
.pp-brand{display:flex;align-items:center;gap:12px;margin:.15rem 0 1.25rem}
.pp-logo{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#134E4A,#0F766E);display:flex;align-items:center;justify-content:center;color:#fff;font-size:20px;font-weight:800;box-shadow:0 7px 18px rgba(15,118,110,.15)}
.pp-brand-name{font-size:1.06rem;font-weight:800;letter-spacing:-.02em;color:var(--pp-ink)}
.pp-brand-tag{font-size:.75rem;color:var(--pp-muted)}
.pp-hero{padding:1.7rem 1.8rem 1.8rem;margin-bottom:1.35rem;border:1px solid var(--pp-border);border-radius:22px;background:linear-gradient(135deg,#fff 0%,#F2F8F7 100%);box-shadow:var(--pp-shadow)}
.pp-kicker{font-size:.7rem;font-weight:800;letter-spacing:.14em;color:var(--pp-accent);margin-bottom:.55rem}
.pp-hero-title{font-size:clamp(2.05rem,4vw,3.35rem);font-weight:850;line-height:1.02;letter-spacing:-.055em;color:var(--pp-ink);max-width:850px}
.pp-hero-sub{margin-top:.85rem;color:var(--pp-muted);font-size:1rem;line-height:1.65;max-width:760px}
.pp-workflow{display:flex;gap:.55rem;flex-wrap:wrap;margin-top:1.15rem}
.pp-step{padding:.42rem .72rem;border:1px solid var(--pp-border);border-radius:999px;background:#fff;color:var(--pp-text);font-size:.76rem;font-weight:700}
.pp-step-active{background:var(--pp-soft);border-color:#B9D8D3;color:var(--pp-navy)}
.pp-section-kicker{font-size:.68rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--pp-accent);margin:1.4rem 0 .3rem}
div[data-testid="stForm"],div[data-testid="stExpander"],div[data-testid="stMetric"]{border:1px solid var(--pp-border);border-radius:16px;background:#fff;box-shadow:var(--pp-shadow);overflow:hidden}
div[data-testid="stForm"]{padding:1.25rem 1.25rem .35rem}
div[data-testid="stExpander"] summary{font-weight:650}
[data-testid="stMetric"]{padding:1rem 1.05rem}
[data-testid="stMetricLabel"]{color:var(--pp-muted);font-size:.76rem;font-weight:650}
[data-testid="stMetricValue"]{color:var(--pp-ink);font-weight:780;letter-spacing:-.03em}
.stButton>button,.stFormSubmitButton>button{border:1px solid rgba(15,118,110,.12);border-radius:11px;min-height:2.75rem;font-weight:700;background:#0F766E;color:#fff;box-shadow:0 6px 16px rgba(15,118,110,.13);transition:transform .16s ease,box-shadow .16s ease}
.stButton>button:hover,.stFormSubmitButton>button:hover{transform:translateY(-1px);box-shadow:0 9px 20px rgba(15,118,110,.18)}
button[kind="secondary"]{background:#fff!important;color:var(--pp-navy)!important;border:1px solid var(--pp-border)!important;box-shadow:none!important}
div[data-baseweb="input"]>div,div[data-baseweb="textarea"]>div,div[data-baseweb="select"]>div{border-radius:10px;border-color:var(--pp-border);background:#fff}
div[data-baseweb="input"]>div:focus-within,div[data-baseweb="textarea"]>div:focus-within,div[data-baseweb="select"]>div:focus-within{border-color:rgba(15,118,110,.72);box-shadow:0 0 0 3px rgba(15,118,110,.09)}
[data-testid="stSidebar"]{background:#F3F8F7;border-right:1px solid var(--pp-border)}
[data-testid="stSidebar"] .stButton>button{width:100%}
hr{border-color:var(--pp-border);margin:1.6rem 0}
[data-testid="stDataFrame"]{border-radius:12px;overflow:hidden;border:1px solid var(--pp-border)}
[data-testid="stAlert"]{border-radius:12px}
div[data-testid="stFileUploader"]{border-radius:12px}
@media(max-width:700px){.block-container{padding-left:.8rem;padding-right:.8rem}.pp-hero{padding:1.25rem}.pp-hero-title{font-size:2rem}.pp-workflow{gap:.4rem}.pp-step{font-size:.7rem;padding:.38rem .6rem}.stButton>button,.stFormSubmitButton>button{width:100%}}
@media(prefers-reduced-motion:reduce){.stButton>button,.stFormSubmitButton>button{transition:none}}
</style>
""", unsafe_allow_html=True)
    st.markdown("""<div class="pp-brand"><div class="pp-logo">P</div><div><div class="pp-brand-name">ProductPulse AI</div><div class="pp-brand-tag">Research → Product → Publish</div></div></div>""", unsafe_allow_html=True)
    st.markdown("""<div class="pp-hero"><div class="pp-kicker">DIGITAL PRODUCT WORKSPACE</div><div class="pp-hero-title">Turn ideas into polished digital products</div><div class="pp-hero-sub">Research opportunities, shape the product, generate content, refine visuals, verify the result, and export with confidence.</div><div class="pp-workflow"><span class="pp-step pp-step-active">01 Research</span><span class="pp-step">02 Product</span><span class="pp-step">03 Content</span><span class="pp-step">04 Visuals</span><span class="pp-step">05 Verify</span><span class="pp-step">06 Export</span></div></div>""", unsafe_allow_html=True)
    try:
        store, product_store = _get_stores()
    except (StorageConfigurationError, StorageConnectionError) as exc:
        st.error(f"Storage is not available: {exc}")
        return
    cookies = stx.CookieManager(key="auth_cookie_manager")
    _restore_cookie_session(store, cookies)
    user_id = _render_auth(store, cookies)
    if not user_id:
        st.warning("Please log in to use the opportunity engine.")
        return
    _restore_builder_state_from_url()
    _render_history(store, user_id)
    storage_label = "Supabase Postgres + private Storage" if load_storage_config().backend == "supabase" else "local SQLite + private files"
    st.sidebar.caption(f"Storage: {storage_label}")
    _render_backup_controls(store, product_store, user_id)

    builder_state = st.session_state.get("product_builder_state")
    if builder_state:
        if st.button("Return to Opportunity Engine", key="return_to_opportunity_engine"):
            _clear_builder_state()
            st.rerun()
        selected_report = store.get(builder_state.get("report_id", ""), user_id=user_id)
        if selected_report is None:
            st.error("The selected research report is not available in this account. Return to the Opportunity Engine and choose it again.")
            return
        try:
            render_product_generator(
                product_store,
                user_id,
                selected_report,
                int(builder_state.get("opportunity_index", -1)),
                builder_state.get("product_id", ""),
            )
        except StorageOperationError:
            st.error("Private storage is temporarily unavailable, so this product page could not be loaded. Nothing was switched to local storage; please try again shortly.")
        return

    st.markdown("""<div class="pp-section-kicker">Creator mode</div>""", unsafe_allow_html=True)
    st.subheader("Create a product from your own topic")
    st.caption("Give ProductPulse AI your topic and description, then optionally add notes or source material. It will use those inputs as the product brief; this mode does not pretend they are market research.")
    with st.form("manual_product_form"):
        manual_topic = st.text_input("Topic", placeholder="e.g., beginner home gardening", key="manual_topic")
        manual_description = st.text_area("What do you want the product to teach or solve?", placeholder="Describe the problem, idea, method, or knowledge you want the product to cover.", height=120, key="manual_description")
        manual_audience = st.text_input("Who is it for? (optional)", placeholder="e.g., complete beginners", key="manual_audience")
        manual_outcome = st.text_area("What should the reader be able to do after using it? (optional)", height=90, key="manual_outcome")
        manual_materials = st.text_area("Related notes, facts, outline, examples, or source material (optional)", placeholder="Paste anything you want the product generator to use. It will not invent citations for this material.", height=150, key="manual_materials")
        manual_files = st.file_uploader("Optional reference files (TXT, MD, CSV, or PDF)", type=["txt", "md", "markdown", "csv", "pdf"], accept_multiple_files=True, key="manual_reference_files")
        manual_submit = st.form_submit_button("Start product from my topic", type="primary")
    if manual_submit:
        if not manual_topic.strip() or not manual_description.strip():
            st.error("Enter both a topic and a description before starting the product.")
        else:
            extracted = _extract_reference_material(manual_files)
            combined_materials = "\n\n".join(part for part in [manual_materials.strip(), extracted] if part)
            manual_report = _build_manual_topic_report(manual_topic, manual_description, manual_audience, manual_outcome, combined_materials, user_id)
            store.save(manual_report, user_id=user_id)
            st.session_state["report"] = manual_report
            _persist_builder_state(manual_report.id, 0, uuid.uuid4().hex)
            st.rerun()

    st.divider()
    st.markdown("""<div class="pp-section-kicker">Research mode</div>""", unsafe_allow_html=True)
    st.subheader("Or run a public research scan")
    st.text_input("Niche, topic, or problem statement", placeholder="e.g., onboarding systems for independent consultants", key="topic_input")
    st.caption("Quick examples")
    example_cols = st.columns(len(QUICK_TOPICS))
    for column, (label, value) in zip(example_cols, QUICK_TOPICS.items()):
        with column:
            st.button(label, key=f"quick_{label.lower().replace(' ', '_')}", on_click=_set_example, args=(value,))

    with st.form("research_form"):
        sources = st.multiselect("Public sources", ["Reddit", "YouTube", "Web", "Quora", "Instagram"], default=["Reddit", "YouTube", "Web", "Quora", "Instagram"])
        limit = st.slider("Maximum items per source", 5, MAX_ITEMS_PER_SOURCE, min(15, MAX_ITEMS_PER_SOURCE))
        submitted = st.form_submit_button("Run opportunity scan")
    if submitted:
        topic = st.session_state.get("topic_input", "").strip()
        if not topic:
            st.error("Enter a niche or problem statement first.")
            return
        if not sources:
            st.error("Choose at least one source.")
            return
        with st.status("Collecting and analyzing public evidence…", expanded=True) as status:
            st.write("Fetching problem-focused source results in parallel.")
            report = run_engine(topic, sources, limit)
            report.user_id = user_id
            st.write(f"Normalized {sum(is_eligible_evidence(item) for item in report.evidence)} eligible evidence records.")
            st.write(f"Extracted {len(report.problems)} problem-language groups (not people or proven recurrence).")
            status.update(label="Opportunity scan complete", state="complete")
        store.save(report, user_id=user_id)
        pdf_path = REPORT_DIR / f"opportunity-report-{report.id}.pdf"
        generate_pdf(report, pdf_path)
        st.session_state["report"] = report
        st.session_state["pdf_path"] = str(pdf_path)

    report = st.session_state.get("report")
    if report:
        st.divider()
        top_score = report.opportunities[0].validation_score if report.opportunities else 0
        eligible_evidence = [item for item in report.evidence if is_eligible_evidence(item)]
        contributors = count_identified_contributors(eligible_evidence)
        metrics = st.columns(4)
        metrics[0].metric("Evidence Records", len(eligible_evidence), help="Collected source records, not unique customers or necessarily independent sources.")
        metrics[1].metric("Problem-Language Groups", len(report.problems), help="Extracted wording patterns; not a count of people or proof of recurrence.")
        metrics[2].metric("Top Heuristic Priority", f"{top_score}/100", help="Ranking heuristic only, not validated demand, a probability, or an outcome-validated score.")
        metrics[3].metric("Identifiable Author/Channel Accounts", contributors if contributors is not None else "Not available", help="Distinct source-local account IDs or names when exposed; not necessarily distinct people across sources.")
        st.caption("Scores prioritize hypotheses for review; they do not validate willingness to pay, sales, or recurrence. Source record volume is not proof of independent customer support.")
        if not report.problems:
            st.warning("No explicit customer pain points were found for this query. Try a more specific topic or include a customer segment, workflow, or frustration.")
        if report.research_audit:
            st.subheader("Research readiness audit")
            st.caption("This audit shows what the scan actually established and what still requires verification. It does not produce a demand score.")
            st.info(report.research_audit.get("research_status", "Research status not available."))
            for check in report.research_audit.get("checks", []):
                status = check.get("status", "REVIEW")
                with st.expander(f"{status} · {check.get('name', 'Research check')}"):
                    st.write(check.get("message", ""))
                    st.caption("Limit: " + check.get("limitation", "Not specified."))
            st.caption("Next validation: " + " · ".join(report.research_audit.get("next_validation", [])))

        st.subheader("Executive summary")
        st.info(report.executive_summary)
        st.subheader("Top hypotheses")
        for opportunity_index, opportunity in enumerate(report.opportunities[:5]):
            with st.expander(f"{opportunity.name} · {opportunity.validation_score}/100"):
                st.write(opportunity.promise)
                st.write(f"**Audience:** {opportunity.audience}")
                st.write(f"**Formats:** {', '.join(opportunity.format)}")
                st.write(f"**Illustrative pricing hypothesis (not validated or based on observed sales):** {opportunity.pricing['starter']} starter · {opportunity.pricing['core']} core · {opportunity.pricing['premium']} premium")
                if report.scoring_version == "evidence_proxy_v2":
                    st.write(f"**Payment-language proxy (0–0.50; not measured willingness to pay):** {opportunity.willingness_to_pay:.2f}")
                    st.write(f"**Evidence coverage input (record volume only):** {opportunity.evidence_strength:.2f}")
                else:
                    st.write("**Legacy stored price-related score:** not directly comparable to the current capped payment-language proxy.")
                st.caption("Heuristic priority score—not a demand probability, validated score, or proof of sales.")
                if opportunity.competition_gap_status == "observed":
                    gap_label = f"Observed heuristic signal · {opportunity.competition_gap:.2f}"
                elif opportunity.competition_gap_status == "insufficient_evidence":
                    gap_label = f"Insufficient evidence (neutral; no gap credit inferred) · {opportunity.competition_gap:.2f}"
                else:
                    gap_label = "Not assessed in this older saved report; stored gap score is unverified"
                st.write(f"**Competition-gap dimension:** {gap_label}")
                st.write(f"**Value hook:** {opportunity.value_hook}")
                st.write(f"**Objection bucket:** {opportunity.objection_bucket}")
                st.write(f"**Pricing rationale:** {opportunity.pricing_rationale}")
                if getattr(opportunity, "validation_findings", None):
                    st.markdown("**Validation notes**")
                    for finding in opportunity.validation_findings:
                        st.write(f"- {finding}")
                if getattr(opportunity, "market_context", None):
                    st.markdown("**Comparable seller/listing context**")
                    for record in opportunity.market_context:
                        st.write(f"- {record}")
                st.write("**Next steps:** " + "; ".join(opportunity.next_steps))
                saved_products = product_store.recent_for_opportunity(user_id, report.id, opportunity_index)
                for saved_product in saved_products:
                    version = saved_product.get("updated_at", "")[:16].replace("T", " ")
                    if st.button(
                        f"Continue saved blueprint · {saved_product['status']} · {version}",
                        key=f"open_product_{saved_product['product_id']}",
                    ):
                        _persist_builder_state(report.id, opportunity_index, saved_product["product_id"])
                        st.rerun()
                if st.button("Generate Product", key=f"generate_product_{report.id}_{opportunity_index}", type="primary"):
                    _persist_builder_state(report.id, opportunity_index, uuid.uuid4().hex)
                    st.rerun()
        st.subheader("Problem-language evidence")
        if report.problems:
            problem_rows = []
            for problem in report.problems:
                problem_rows.append({
                    "Extracted language pattern": problem.problem,
                    "Sentence mentions": problem.sentence_mentions if problem.sentence_mentions is not None else problem.frequency,
                    "Supporting evidence items": problem.evidence_items if problem.evidence_items is not None else "Not recorded in this older report",
                    "Identified author/channel accounts": problem.unique_contributors if problem.unique_contributors is not None else "Not available / not recorded",
                })
            st.dataframe(problem_rows, use_container_width=True, hide_index=True)
        st.caption("Sentence mentions are occurrences in collected text. Evidence-item counts deduplicate source records within each pattern; author/channel IDs are source-local where available, and do not establish unique people or recurrence over time.")

        st.subheader("Classified problem-language excerpts (heuristic)")
        matrix_cols = st.columns(3)
        for column, (bucket, problems) in zip(matrix_cols, report.objection_matrix.items()):
            with column:
                st.markdown(f"**{bucket}**")
                for problem in problems:
                    st.write(f"- {problem}")
                if not problems:
                    st.caption("No explicit signals")
        st.subheader("Hormozi Sales Hooks")
        for hook in report.sales_hooks:
            st.info(hook)
        st.subheader("Competition-gap evidence")
        gap_analysis = report.gap_analysis
        if not gap_analysis:
            st.info("This saved report predates gap-evidence tracking. Its competition-gap score cannot be verified from a saved audit; run a new scan for evidence-linked themes.")
        elif gap_analysis.get("status") == "observed" and gap_analysis.get("gaps"):
            st.caption(gap_analysis.get("assessment", "Matched evidence is heuristic, not proof of unmet demand."))
            st.dataframe([{"Theme": item["gap"], "Matching evidence items": item.get("evidence_items", item.get("mentions", 0)), "Observed excerpt": item["example"], "Source": item.get("source", ""), "URL": item.get("url", "")} for item in gap_analysis["gaps"]], use_container_width=True, hide_index=True)
        else:
            st.info("Insufficient evidence: no recognized competition-gap terms were found. No generic gap examples were added, and the competition-gap score is neutral rather than positive evidence.")
        st.subheader("Marketplace listing signals")
        if report.marketplace_gaps:
            st.dataframe([{"Marketplace": gap.marketplace, "Product": gap.title, "Observed price": gap.price or "Not found", "Format benchmark (estimate)": gap.price_benchmark or "Not available", "Format": gap.format, "Rating": gap.rating, "Gap signal": gap.gap_signal, "URL": gap.url} for gap in report.marketplace_gaps], use_container_width=True, hide_index=True)
            st.caption("Listing prices come from scraped marketplace text. Format benchmarks are heuristic estimates, not observed prices; listing presence alone is not evidence of a product gap.")
            with st.expander("Marketplace review insights"):
                for gap in report.marketplace_gaps:
                    st.markdown(f"**{gap.marketplace}: {gap.title}**")
                    for insight in gap.review_insights:
                        st.write(f"- {insight}")
        else:
            st.info("No public Etsy or Gumroad marketplace results were available. The blueprint below is based on customer pain evidence only.")
        st.subheader("Day-1 Deliverable Blueprint")
        blueprint = report.product_blueprint
        if blueprint:
            st.write(f"**Product:** {blueprint.get('product_name', 'Top opportunity')}")
            st.write(f"**Promise:** {blueprint.get('one_sentence_promise', '')}")
            for module in blueprint.get("modules", []):
                with st.expander(module.get("name", "Module")):
                    for page in module.get("pages", []):
                        st.write(f"- {page}")
            st.write("**Bonuses:** " + "; ".join(blueprint.get("bonuses", [])))
            st.write("**Build order:** " + " → ".join(blueprint.get("day_one_build_order", [])))
        else:
            st.info("A product blueprint will appear when at least one explicit opportunity is found.")
        st.subheader("Launch & Marketing Kit")
        kit = report.launch_kit
        if kit:
            st.markdown("**Listing description**")
            st.write(kit.get("listing_description", ""))
            st.markdown("**SEO tags**")
            st.write(", ".join(kit.get("seo_tags", [])))
            st.markdown("**Short-form video hooks**")
            for hook in kit.get("short_form_hooks", []):
                st.info(hook)
            st.markdown("**ROI justification**")
            st.write(kit.get("roi_justification", ""))
            if kit.get("marketplace_benchmark_note"):
                st.caption(kit["marketplace_benchmark_note"])
        else:
            st.info("The launch kit will appear when at least one explicit opportunity is found.")
        st.subheader("Raw source links")
        _render_source_links(report)
        with st.expander("Collection notes"):
            for note in report.collection_notes:
                st.write(note)
        st.subheader("Download report")
        with open(st.session_state["pdf_path"], "rb") as handle:
            st.download_button("Download styled PDF report", handle, file_name=Path(st.session_state["pdf_path"]).name, mime="application/pdf")


if __name__ == "__main__":
    render()
