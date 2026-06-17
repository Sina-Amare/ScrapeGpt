"""
Phase 2.5 E2E Validation Script

All DB seeding happens in a single asyncio.run() call before the backend starts,
avoiding asyncpg pool contention with the running server.

Usage:
    python tests/validation/run_validation.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=False)

API_BASE = "http://127.0.0.1:8000/api/v1"
FIXTURE_PORT = 9877
FIXTURE_BASE = f"http://127.0.0.1:{FIXTURE_PORT}"
TEST_EMAIL = "validation@example.com"
TEST_PASSWORD = "ValidationTest123!"
BACKEND_STARTUP_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validation")


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool = False
    evidence: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.evidence.append(f"  PASS {msg}")

    def fail(self, msg: str) -> None:
        self.bugs.append(f"  FAIL {msg}")

    def info(self, msg: str) -> None:
        self.evidence.append(f"  INFO {msg}")


ALL_RESULTS: list[ScenarioResult] = []


# ---------------------------------------------------------------------------
# Fixture server
# ---------------------------------------------------------------------------

FIXTURE_ROUTES: dict[str, str] = {
    "/": "current_page.html",
    "/food/potato-products": "pagination_page1.html",
    "/food/pizza": "unrelated_pizza.html",
    "/food/meat": "unrelated_pizza.html",
    "/food/beer": "unrelated_pizza.html",
    "/food/fruit": "unrelated_pizza.html",
    "/products": "products_list.html",
    "/products/1": "product_detail_1.html",
    "/products/2": "product_detail_2.html",
    "/products/3": "product_detail_3.html",
    "/about": "unrelated_pizza.html",
    "/contact": "unrelated_pizza.html",
    "/blog": "unrelated_pizza.html",
    "/food/potato-products?page=2": "pagination_page2.html",
    "/food/potato-products?page=3": "pagination_page3.html",
}


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self) -> None:
        path_part = self.path.split("?")[0]
        full = self.path

        filename: str | None = FIXTURE_ROUTES.get(full) or FIXTURE_ROUTES.get(path_part)

        if filename is None:
            if path_part == "/robots.txt":
                self._respond(200, "text/plain", b"User-agent: *\nAllow: /\n")
                return
            self.send_response(404)
            self.end_headers()
            return

        filepath = FIXTURES_DIR / filename
        if not filepath.exists():
            self.send_response(404)
            self.end_headers()
            return
        self._respond(200, "text/html; charset=utf-8", filepath.read_bytes())

    def _respond(self, code: int, ct: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fixture_server() -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", FIXTURE_PORT), FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Fixture server at %s", FIXTURE_BASE)
    return server


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

_backend_proc: subprocess.Popen[bytes] | None = None


def _kill_port_8000() -> None:
    """Kill any process holding port 8000."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if ":8000 " in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit():
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, timeout=5)
                    log.info("Killed process %s holding port 8000", pid)
    except Exception:
        pass
    time.sleep(1.0)


def start_backend() -> bool:
    global _backend_proc
    _kill_port_8000()
    env = os.environ.copy()
    env["ALLOW_PRIVATE_NETWORK_URLS"] = "true"
    env["ROBOTS_FAILURE_POLICY"] = "allow"
    env["MIN_CRAWL_DELAY_MS"] = "0"
    env["CRAWL_CONCURRENCY"] = "5"
    env["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"

    log_path = REPO_ROOT / "tests" / "validation" / "backend.log"
    log_fh = open(log_path, "w")
    _backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "8000", "--no-access-log"],
        cwd=str(REPO_ROOT), env=env,
        stdout=log_fh, stderr=log_fh,
    )
    deadline = time.time() + BACKEND_STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            r = httpx.get("http://127.0.0.1:8000/api/v1/health/live", timeout=2.0)
            if r.status_code == 200:
                log.info("Backend healthy")
                return True
        except Exception:
            pass
        if _backend_proc.poll() is not None:
            log_fh.flush()
            log.error("Backend exited early — see %s", log_path)
            return False
        time.sleep(0.5)
    log.error("Backend timeout after %ds — see %s", BACKEND_STARTUP_TIMEOUT, log_path)
    return False


def stop_backend() -> None:
    if _backend_proc and _backend_proc.poll() is None:
        _backend_proc.terminate()
        try:
            _backend_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _backend_proc.kill()


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class APIClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self.http = httpx.Client(timeout=httpx.Timeout(30.0))

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def register(self, email: str, password: str) -> httpx.Response:
        return self.http.post(f"{API_BASE}/auth/register",
                              json={"email": email, "password": password})

    def login(self, email: str, password: str) -> bool:
        r = self.http.post(f"{API_BASE}/auth/login",
                           data={"username": email, "password": password})
        if r.status_code == 200:
            self._token = r.json()["access_token"]
            return True
        log.error("Login failed %d: %s", r.status_code, r.text[:200])
        return False

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(f"{API_BASE}{path}", headers=self._h(), **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.post(f"{API_BASE}{path}", headers=self._h(), **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.patch(f"{API_BASE}{path}", headers=self._h(), **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.delete(f"{API_BASE}{path}", headers=self._h(), **kw)


def get_409_code(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail") or {}
        return detail.get("error_code", "") if isinstance(detail, dict) else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# DB config
# ---------------------------------------------------------------------------

def get_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    return url


# ---------------------------------------------------------------------------
# Scope / field helpers
# ---------------------------------------------------------------------------

def make_scope(mode: str, status: str = "SYSTEM_DEFAULTED",
               confirmed: bool = False) -> dict[str, Any]:
    return {
        "version": 1, "mode": mode,
        "status": "USER_CONFIRMED" if confirmed else status,
        "seed_url": None, "max_pages": 10, "max_depth": None,
        "include_patterns": [], "exclude_patterns": [],
        "pagination": {}, "link_rules": [], "ai_recommendation": None,
        "user_confirmed_at": datetime.now(timezone.utc).isoformat() if confirmed else None,
    }


SAMPLE_FIELDS: list[dict[str, Any]] = [
    {"name": "product_name", "label": "Product name", "user_label": "Product name",
     "selector": ".product-name", "type": "string", "selected": True,
     "required": True, "confidence": 0.95, "sample_values": ["Widget A"], "warnings": []},
    {"name": "price", "label": "Price", "user_label": "Price",
     "selector": ".product-price", "type": "string", "selected": True,
     "required": True, "confidence": 0.92, "sample_values": ["$3.49"], "warnings": []},
    {"name": "category", "label": "Category", "user_label": "Category",
     "selector": ".product-category", "type": "string", "selected": True,
     "required": False, "confidence": 0.78, "sample_values": ["Frozen"], "warnings": []},
]


# ---------------------------------------------------------------------------
# Single-pass DB setup (called ONCE before backend starts)
# ---------------------------------------------------------------------------

@dataclass
class TestData:
    user_id: int
    e2e1_project_id: int
    e2e2_project_id: int
    e2e3_project_id: int
    e2e4_project_id: int
    pagination_project_id: int
    export_project_id: int
    failure_no_preview_id: int
    failure_unconf_id: int


async def _setup_all(db_url: str) -> TestData:
    """Create user + all seeded projects in one pass with one engine."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import select
    from app.models.user import User
    from app.core.security import hash_password as get_password_hash
    from app.models.job import (
        ExtractionMode, ExtractionSpec, ExtractedRecord, ExtractionRun,
        ExtractionRunState, Project,
        ProjectState, RenderMode, WorkflowMode, DEFAULT_CRAWL_SCOPE,
    )

    def _mk_run(project_id: int, spec_id: int, total: int) -> ExtractionRun:
        now = datetime.now(timezone.utc)
        return ExtractionRun(
            project_id=project_id, spec_id=spec_id,
            state=ExtractionRunState.COMPLETED.value,
            started_at=now, finished_at=now, total_records=total,
        )

    engine = create_async_engine(db_url, echo=False, pool_size=2, max_overflow=0)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_or_create_user(db: Any) -> int:
        result = await db.execute(select(User).where(User.email == TEST_EMAIL))
        user = result.scalar_one_or_none()
        if user:
            return user.id
        hashed = get_password_hash(TEST_PASSWORD)
        user = User(email=TEST_EMAIL, hashed_password=hashed, is_active=True)
        db.add(user)
        await db.flush()
        return user.id

    def _mk_project(user_id: int, url: str, state: ProjectState,
                    analysis: dict | None = None) -> Project:
        return Project(
            user_id=user_id, url=url, normalized_url=url,
            state=state,
            extraction_mode=ExtractionMode.STRUCTURED,
            workflow_mode=WorkflowMode.GUIDED,
            render_mode=RenderMode.AUTO,
            confidence=0.85, warnings=[],
            analysis=analysis or {"page_type": "product_listing", "confidence": 0.85,
                                   "repeated_item_selector": ".item",
                                   "candidate_fields": SAMPLE_FIELDS,
                                   "detail_link_selector": None,
                                   "pagination_selector": None,
                                   "estimated_pages": 3, "warnings": []},
            fetch_metadata={"status_code": 200, "content_type": "text/html"},
        )

    def _mk_spec(project_id: int, crawl_scope: dict,
                 quality_summary: dict | None = None, page_limit: int = 10) -> ExtractionSpec:
        return ExtractionSpec(
            project_id=project_id,
            mode=ExtractionMode.STRUCTURED,
            fields=SAMPLE_FIELDS,
            content_config={}, url_patterns=[],
            page_limit=page_limit, export_format="csv",
            crawl_scope=crawl_scope,
            quality_summary=quality_summary,
        )

    async with Session() as db:
        async with db.begin():
            user_id = await _get_or_create_user(db)

            # E2E-1: CURRENT_PAGE, unconfirmed (no confirmation needed)
            p1 = _mk_project(user_id, f"{FIXTURE_BASE}/", ProjectState.ANALYSIS_READY)
            db.add(p1)
            await db.flush()
            db.add(_mk_spec(p1.id, make_scope("CURRENT_PAGE", "SYSTEM_DEFAULTED")))

            # E2E-2: PAGINATION, confirmed
            p2 = _mk_project(user_id, f"{FIXTURE_BASE}/food/potato-products",
                              ProjectState.ANALYSIS_READY)
            db.add(p2)
            await db.flush()
            db.add(_mk_spec(p2.id, make_scope("PAGINATION", confirmed=True)))

            # E2E-3: DATASET, confirmed, with link_rules
            scope3 = make_scope("DATASET", confirmed=True)
            scope3["link_rules"] = [{
                "role": "detail", "action": "include",
                "selector": "a[href^='/products/']",
                "pattern": "/products/", "reason": "Product detail pages",
                "confidence": 0.88,
            }]
            p3 = _mk_project(user_id, f"{FIXTURE_BASE}/products",
                              ProjectState.ANALYSIS_READY)
            db.add(p3)
            await db.flush()
            db.add(_mk_spec(p3.id, scope3))

            # E2E-4: FULL_SITE, NOT confirmed
            p4 = _mk_project(user_id, f"{FIXTURE_BASE}/", ProjectState.ANALYSIS_READY)
            db.add(p4)
            await db.flush()
            db.add(_mk_spec(p4.id, make_scope("FULL_SITE", "AI_SUGGESTED")))

            # Pagination: COMPLETED with 1000 records
            quality = {
                "overall": "good",
                "field_success_rates": {
                    "product_name": 1.0, "price": 1.0, "category": 1.0},
                "missing_field_rates": {
                    "product_name": 0.0, "price": 0.0, "category": 0.0},
                "warnings": [],
            }
            completed_scope = {
                **DEFAULT_CRAWL_SCOPE,
                "mode": "CURRENT_PAGE", "status": "USER_CONFIRMED",
                "user_confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
            p_pag = _mk_project(user_id, f"{FIXTURE_BASE}/products",
                                 ProjectState.COMPLETED)
            db.add(p_pag)
            await db.flush()
            pag_spec = _mk_spec(p_pag.id, completed_scope,
                                quality_summary=quality, page_limit=500)
            db.add(pag_spec)
            await db.flush()
            # Run-scoped: records belong to a COMPLETED run that the project
            # promotes via current_extraction_run_id (reads are run-scoped, so
            # without this the read endpoints would return zero records).
            pag_run = _mk_run(p_pag.id, pag_spec.id, 1000)
            db.add(pag_run)
            await db.flush()
            p_pag.current_extraction_run_id = pag_run.id
            for i in range(1, 1001):
                db.add(ExtractedRecord(
                    project_id=p_pag.id,
                    extraction_run_id=pag_run.id,
                    record_ordinal=i - 1,
                    source_url=f"{FIXTURE_BASE}/products",
                    raw_data={"product_name": f"Product {i}",
                              "price": f"${i * 0.99:.2f}",
                              "category": ["Electronics", "Food", "Books", "Clothing"][i % 4]},
                    normalized_data={"product_name": f"Product {i}",
                                     "price": f"${i * 0.99:.2f}",
                                     "category": ["Electronics", "Food", "Books", "Clothing"][i % 4]},
                    warnings=[],
                ))

            # Export: COMPLETED with 3 records. Also seed an OLDER completed run
            # whose records must stay invisible — proves reads are scoped to
            # current_extraction_run_id (non-destructive re-extraction).
            p_exp = _mk_project(user_id, f"{FIXTURE_BASE}/products",
                                 ProjectState.COMPLETED)
            db.add(p_exp)
            await db.flush()
            exp_spec = _mk_spec(p_exp.id, completed_scope, page_limit=100)
            db.add(exp_spec)
            await db.flush()
            stale_run = _mk_run(p_exp.id, exp_spec.id, 2)
            db.add(stale_run)
            await db.flush()
            for ordinal, name in enumerate(["STALE Widget", "STALE Gadget"]):
                db.add(ExtractedRecord(
                    project_id=p_exp.id,
                    extraction_run_id=stale_run.id, record_ordinal=ordinal,
                    source_url=f"{FIXTURE_BASE}/products",
                    raw_data={"product_name": name, "price": "$0.00", "category": "Stale"},
                    normalized_data={"product_name": name, "price": "$0.00", "category": "Stale"},
                    warnings=[],
                ))
            exp_run = _mk_run(p_exp.id, exp_spec.id, 3)
            db.add(exp_run)
            await db.flush()
            p_exp.current_extraction_run_id = exp_run.id
            for ordinal, (name, price, cat) in enumerate([
                ("Alpha Widget", "$9.99", "Electronics"),
                ("Beta Gadget", "$14.99", "Electronics"),
                ("Gamma Tool", "$4.99", "Tools"),
            ]):
                db.add(ExtractedRecord(
                    project_id=p_exp.id,
                    extraction_run_id=exp_run.id, record_ordinal=ordinal,
                    source_url=f"{FIXTURE_BASE}/products",
                    raw_data={"product_name": name, "price": price, "category": cat},
                    normalized_data={"product_name": name, "price": price, "category": cat},
                    warnings=[],
                ))

            # Failure scenarios: no preview project
            p_noprev = _mk_project(user_id, f"{FIXTURE_BASE}/",
                                    ProjectState.ANALYSIS_READY)
            db.add(p_noprev)
            await db.flush()
            db.add(_mk_spec(p_noprev.id, make_scope("CURRENT_PAGE")))

            # Failure scenarios: FULL_SITE unconfirmed (separate from E2E-4)
            p_unconf = _mk_project(user_id, f"{FIXTURE_BASE}/",
                                    ProjectState.ANALYSIS_READY)
            db.add(p_unconf)
            await db.flush()
            db.add(_mk_spec(p_unconf.id, make_scope("FULL_SITE", "AI_SUGGESTED")))

            await db.flush()

            result = TestData(
                user_id=user_id,
                e2e1_project_id=p1.id,
                e2e2_project_id=p2.id,
                e2e3_project_id=p3.id,
                e2e4_project_id=p4.id,
                pagination_project_id=p_pag.id,
                export_project_id=p_exp.id,
                failure_no_preview_id=p_noprev.id,
                failure_unconf_id=p_unconf.id,
            )

    await engine.dispose()
    return result


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_e2e_1(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("E2E-1: Current Page Only")
    pid = td.e2e1_project_id

    try:
        r = client.get(f"/projects/{pid}")
        assert r.status_code == 200, f"GET project {r.status_code}: {r.text[:200]}"
        proj = r.json()
        assert proj["system_state"] == "ANALYSIS_READY"
        assert proj["spec"]["crawl_scope"]["mode"] == "CURRENT_PAGE"
        s.ok(f"GET project {pid}: ANALYSIS_READY + CURRENT_PAGE scope")

        r2 = client.post(f"/projects/{pid}/frontier-preview")
        assert r2.status_code == 201, f"POST frontier-preview {r2.status_code}: {r2.text[:300]}"
        preview = r2.json()
        s.ok(f"POST /frontier-preview: 201, id={preview['id']}")

        included = [d["url"] for d in preview["included_urls"]]
        excluded_codes = {d["reason_code"] for d in preview["excluded_urls"]}
        s.info(f"Included ({len(included)}): {included}")
        s.info(f"Excluded reason codes: {excluded_codes}")

        assert any(FIXTURE_BASE in u for u in included), f"Seed URL not in included: {included}"
        s.ok("Seed URL in included_urls")

        r3 = client.get(f"/projects/{pid}/frontier-preview")
        assert r3.status_code == 200 and r3.json()["id"] == preview["id"]
        s.ok("GET /frontier-preview returns cached preview")

        r4 = client.post(f"/projects/{pid}/extract", json={"extract_anyway": True})
        code = get_409_code(r4)
        if code == "SCOPE_NOT_CONFIRMED":
            s.fail("CURRENT_PAGE extraction blocked by SCOPE_NOT_CONFIRMED")
        else:
            s.ok(f"POST /extract: {r4.status_code} — CURRENT_PAGE not scope-gated")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_e2e_2(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("E2E-2: Pagination Scope")
    pid = td.e2e2_project_id

    try:
        r = client.post(f"/projects/{pid}/frontier-preview")
        assert r.status_code == 201, f"{r.status_code}: {r.text[:300]}"
        preview = r.json()
        s.ok("POST /frontier-preview: 201")

        included = [d["url"] for d in preview["included_urls"]]
        excluded = [(d["url"], d["reason_code"]) for d in preview["excluded_urls"]]
        s.info(f"Included ({len(included)}): {included[:6]}")
        s.info(f"Excluded ({len(excluded)}): {excluded[:6]}")

        assert any("potato-products" in u for u in included), f"Seed not included: {included}"
        s.ok("Seed URL (potato-products) is included")

        cats = ["pizza", "meat", "beer", "fruit"]
        excluded_cat = [(u, c) for u, c in excluded if any(cat in u for cat in cats)]
        if excluded_cat:
            s.ok(f"Category links excluded ({len(excluded_cat)}): {[u for u, _ in excluded_cat[:3]]}")
        else:
            s.info("Category links not found in excluded (may not appear in seed page source)")

        r2 = client.get(f"/projects/{pid}")
        assert r2.json()["spec"]["crawl_scope"]["status"] == "USER_CONFIRMED"
        s.ok("PAGINATION scope is USER_CONFIRMED")

        r3 = client.post(f"/projects/{pid}/extract", json={"extract_anyway": True})
        if get_409_code(r3) == "SCOPE_NOT_CONFIRMED":
            s.fail("PAGINATION extraction blocked despite USER_CONFIRMED")
        else:
            s.ok(f"Extraction not blocked by scope gate: {r3.status_code}")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_e2e_3(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("E2E-3: Dataset Scope")
    pid = td.e2e3_project_id

    try:
        r = client.post(f"/projects/{pid}/frontier-preview")
        assert r.status_code == 201, f"{r.status_code}: {r.text[:300]}"
        preview = r.json()
        s.ok("POST /frontier-preview: 201")

        included = [d["url"] for d in preview["included_urls"]]
        excluded = [(d["url"], d["reason_code"]) for d in preview["excluded_urls"]]
        s.info(f"Included ({len(included)}): {included}")
        s.info(f"Excluded ({len(excluded)}): {excluded[:5]}")

        assert any("/products" in u for u in included), f"Seed not included: {included}"
        s.ok("Seed URL /products is included")

        detail_inc = [u for u in included if "/products/" in u]
        if detail_inc:
            s.ok(f"Detail pages in included: {detail_inc}")
        else:
            s.info("Detail pages not in preview (link_rules applied at crawl time)")

        nav_exc = [(u, c) for u, c in excluded if any(n in u for n in ["about", "contact", "blog"])]
        if nav_exc:
            s.ok(f"Navigation pages excluded: {[u for u, _ in nav_exc]}")
        else:
            s.info("Nav pages not in excluded sample")

        r2 = client.get(f"/projects/{pid}")
        assert r2.json()["spec"]["crawl_scope"]["status"] == "USER_CONFIRMED"
        s.ok("DATASET scope is USER_CONFIRMED")

        r3 = client.post(f"/projects/{pid}/extract", json={"extract_anyway": True})
        if get_409_code(r3) == "SCOPE_NOT_CONFIRMED":
            s.fail("DATASET extraction blocked despite USER_CONFIRMED")
        else:
            s.ok(f"Extraction not blocked: {r3.status_code}")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_e2e_4(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("E2E-4: Full Site Confirmation Gate")
    pid = td.e2e4_project_id

    try:
        # Must be blocked before confirmation
        r1 = client.post(f"/projects/{pid}/extract", json={"extract_anyway": True})
        if r1.status_code == 409 and get_409_code(r1) == "SCOPE_NOT_CONFIRMED":
            s.ok("POST /extract -> 409 SCOPE_NOT_CONFIRMED when FULL_SITE unconfirmed")
        elif r1.status_code == 409:
            s.info(f"409 code={get_409_code(r1)}: {r1.json().get('detail')}")
            s.ok("POST /extract blocked with 409")
        else:
            s.fail(f"Expected 409 SCOPE_NOT_CONFIRMED, got {r1.status_code}: {r1.text[:150]}")

        # GET shows unconfirmed state
        r2 = client.get(f"/projects/{pid}")
        cs = r2.json()["spec"]["crawl_scope"]
        assert cs["mode"] == "FULL_SITE"
        assert cs["status"] == "AI_SUGGESTED"
        assert cs["user_confirmed_at"] is None
        s.ok("Project shows FULL_SITE / AI_SUGGESTED / null user_confirmed_at")

        # PATCH to confirm
        confirmed = {**cs, "status": "USER_CONFIRMED",
                     "user_confirmed_at": datetime.now(timezone.utc).isoformat()}
        r3 = client.patch(f"/projects/{pid}/spec", json={"crawl_scope": confirmed})
        assert r3.status_code == 200, f"PATCH failed {r3.status_code}: {r3.text[:200]}"
        updated = r3.json()
        assert updated["crawl_scope"]["status"] == "USER_CONFIRMED"
        assert updated["crawl_scope"]["user_confirmed_at"] is not None
        s.ok("PATCH /spec confirms FULL_SITE scope")

        # Extraction now allowed
        r4 = client.post(f"/projects/{pid}/extract", json={"extract_anyway": True})
        if get_409_code(r4) == "SCOPE_NOT_CONFIRMED":
            s.fail("Extraction still blocked after confirmation")
        else:
            s.ok(f"Extraction accepted after confirmation: {r4.status_code}")

        # Frontier preview for confirmed FULL_SITE
        r5 = client.post(f"/projects/{pid}/frontier-preview")
        if r5.status_code == 201:
            fp = r5.json()
            s.ok(f"Frontier preview: {len(fp['included_urls'])} included, "
                 f"{len(fp['excluded_urls'])} excluded")
        else:
            s.info(f"Frontier preview: {r5.status_code} (project may be active)")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_pagination_api(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("PAGINATION: Records Page API (1000 records)")
    pid = td.pagination_project_id

    try:
        r = client.get(f"/projects/{pid}/records-page?skip=0&limit=100")
        assert r.status_code == 200, f"records-page {r.status_code}: {r.text[:200]}"
        page = r.json()
        assert page["total"] == 1000, f"total={page['total']}, expected 1000"
        assert page["has_more"] is True
        assert page["next_skip"] == 100
        assert len(page["items"]) == 100
        s.ok("Page 1: total=1000, has_more=True, items=100, next_skip=100")

        r2 = client.get(f"/projects/{pid}/records-page?skip=100&limit=100")
        p2 = r2.json()
        assert len(p2["items"]) == 100 and p2["has_more"] is True
        s.ok("Page 2: items=100, has_more=True")

        r3 = client.get(f"/projects/{pid}/records-page?skip=900&limit=100")
        p3 = r3.json()
        assert len(p3["items"]) == 100
        assert p3["has_more"] is False, f"Last page has_more={p3['has_more']}"
        assert p3["next_skip"] is None, f"Last page next_skip={p3['next_skip']}"
        s.ok("Last page: has_more=False, next_skip=None")

        r4 = client.get(f"/projects/{pid}/records-page?skip=0&limit=50")
        assert len(r4.json()["items"]) == 50 and r4.json()["next_skip"] == 50
        s.ok("limit=50: items=50, next_skip=50")

        r5 = client.get(f"/projects/{pid}/records-page?skip=0&limit=500")
        assert r5.status_code == 200 and len(r5.json()["items"]) == 500
        s.ok("limit=500: items=500")

        r6 = client.get(f"/projects/{pid}/records-page?skip=0&limit=501")
        if r6.status_code == 422:
            s.ok("limit=501 -> 422 validation error")
        else:
            count = len(r6.json().get("items", []))
            assert count <= 500
            s.ok(f"limit=501 clamped to {count}")

        assert "columns" in page
        s.ok(f"columns present: {page['columns'][:5]}")

        r7 = client.get(f"/projects/{pid}")
        eq = r7.json().get("extraction_quality")
        if eq:
            assert eq["overall"] == "good"
            s.ok(f"extraction_quality.overall={eq['overall']}")
        else:
            s.info("extraction_quality not in project response")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_export(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("EXPORT: CSV, JSON, XLSX")
    pid = td.export_project_id

    try:
        r_csv = client.get(f"/projects/{pid}/export?format=csv")
        assert r_csv.status_code == 200, f"CSV {r_csv.status_code}"
        lines = [l for l in r_csv.text.strip().splitlines() if l]
        assert len(lines) >= 2
        for name in ["Alpha Widget", "Beta Gadget", "Gamma Tool"]:
            assert name in r_csv.text, f"'{name}' missing from CSV"
        s.ok(f"CSV: {len(lines)} lines, all 3 records present. Header: {lines[0][:80]}")

        r_json = client.get(f"/projects/{pid}/export?format=json")
        assert r_json.status_code == 200, f"JSON {r_json.status_code}"
        try:
            json_data = r_json.json()
            if not isinstance(json_data, list):
                json_data = [json.loads(l) for l in r_json.text.strip().splitlines() if l]
        except Exception:
            json_data = [json.loads(l) for l in r_json.text.strip().splitlines() if l]
        assert len(json_data) == 3, f"JSON expected 3, got {len(json_data)}"
        s.ok(f"JSON: {len(json_data)} records")

        r_xlsx = client.get(f"/projects/{pid}/export?format=xlsx")
        assert r_xlsx.status_code == 200, f"XLSX {r_xlsx.status_code}"
        ct = r_xlsx.headers.get("content-type", "")
        assert any(kw in ct for kw in ("spreadsheet", "xlsx", "octet")), \
            f"Unexpected content-type: {ct}"
        s.ok(f"XLSX: 200, content-type={ct[:80]}")

        r_page = client.get(f"/projects/{pid}/records-page?skip=0&limit=100")
        assert r_page.json()["total"] == 3
        s.ok("records-page total (3) matches export count")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_failure_states(client: APIClient, td: TestData) -> ScenarioResult:
    s = ScenarioResult("FAILURE_STATES")

    try:
        # F-1: No provider -> analyze returns 409
        r1 = client.post("/projects/analyze", json={"url": "http://example.com"})
        if r1.status_code == 409 and get_409_code(r1) == "NO_PROVIDER_CONFIGURED":
            s.ok("POST /analyze with no provider -> 409 NO_PROVIDER_CONFIGURED")
        elif r1.status_code == 202:
            s.info("analyze returned 202 — provider is configured")
        else:
            s.info(f"analyze returned {r1.status_code}: {r1.text[:100]}")

        # F-2: Extract non-existent project -> 404
        r2 = client.post("/projects/9999999/extract", json={"extract_anyway": True})
        assert r2.status_code == 404, f"Expected 404, got {r2.status_code}"
        s.ok("POST /extract on non-existent project -> 404")

        # F-3: GET frontier-preview when none exists -> 404
        pid = td.failure_no_preview_id
        r3 = client.get(f"/projects/{pid}/frontier-preview")
        assert r3.status_code == 404, f"Expected 404, got {r3.status_code}"
        s.ok("GET /frontier-preview with no preview -> 404")

        # F-4: records-page on non-existent project -> 404
        r4 = client.get("/projects/9999999/records-page?skip=0&limit=100")
        assert r4.status_code == 404, f"Expected 404, got {r4.status_code}"
        s.ok("GET /records-page on non-existent project -> 404")

        # F-5: records-page with skip=-1 -> 422
        r5 = client.get(f"/projects/{pid}/records-page?skip=-1&limit=100")
        assert r5.status_code == 422, f"Expected 422, got {r5.status_code}"
        s.ok("GET /records-page with skip=-1 -> 422")

        # F-6: SCOPE_NOT_CONFIRMED 409 body structure
        pid2 = td.failure_unconf_id
        r6 = client.post(f"/projects/{pid2}/extract", json={"extract_anyway": True})
        if r6.status_code == 409:
            detail = r6.json().get("detail") or {}
            assert isinstance(detail, dict), f"detail not dict: {detail}"
            assert detail.get("error_code") == "SCOPE_NOT_CONFIRMED", f"code: {detail}"
            assert "scope" in detail, f"missing scope: {detail}"
            assert "message" in detail, f"missing message: {detail}"
            s.ok("SCOPE_NOT_CONFIRMED 409 body: error_code + scope + message present")
        else:
            s.fail(f"Expected 409 for FULL_SITE unconfirmed, got {r6.status_code}")

        # F-7: Unauthenticated -> 401
        r7 = httpx.get(f"{API_BASE}/projects", timeout=10.0)
        assert r7.status_code == 401, f"Expected 401, got {r7.status_code}"
        s.ok("GET /projects without auth -> 401")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_run_scoping(client: APIClient, td: TestData) -> ScenarioResult:
    """Reads are scoped to current_extraction_run_id: an older completed run's
    records (seeded as 'STALE *') must never surface (T1, migration 013)."""
    s = ScenarioResult("RUN-SCOPING: non-destructive run-scoped reads")
    pid = td.export_project_id
    try:
        r = client.get(f"/projects/{pid}")
        assert r.status_code == 200, f"GET project {r.status_code}: {r.text[:200]}"
        run_id = r.json().get("current_extraction_run_id")
        if run_id:
            s.ok(f"current_extraction_run_id exposed ({run_id})")
        else:
            s.fail("current_extraction_run_id missing on a completed project")

        rp = client.get(f"/projects/{pid}/records-page?skip=0&limit=100")
        assert rp.status_code == 200, f"records-page {rp.status_code}: {rp.text[:200]}"
        body = rp.json()
        total = body.get("total")
        if total == 3:
            s.ok("records-page total == 3 (current run only; older run hidden)")
        else:
            s.fail(f"records-page total == {total}, expected 3 (run-scoping leak)")

        names = {
            (row.get("normalized_data") or {}).get("product_name")
            for row in body.get("items", [])
        }
        if not any(str(n).startswith("STALE") for n in names):
            s.ok("older run's records are not surfaced through reads")
        else:
            s.fail(f"stale-run records leaked into reads: {sorted(names)}")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


def scenario_provider(client: APIClient) -> ScenarioResult:
    s = ScenarioResult("PROVIDER: Configuration Check")

    try:
        r = client.get("/providers")
        assert r.status_code == 200
        providers = r.json()
        s.info(f"Provider count: {len(providers)}")

        if providers:
            for p in providers:
                s.info(f"  {p['name']} ({p.get('provider', '?')} / {p.get('model', '?')})")
            s.ok(f"{len(providers)} provider(s) configured")
            r2 = client.post(f"/providers/{providers[0]['id']}/test")
            if r2.status_code == 200:
                result = r2.json()
                if result.get("ok"):
                    s.ok("Provider test: ok=true")
                else:
                    s.info(f"Provider test: ok=false, error={result.get('error')}")
        else:
            s.ok("No providers configured (expected without API keys)")

        s.passed = len(s.bugs) == 0
    except Exception as exc:
        s.bugs.append(f"Exception: {exc}\n{traceback.format_exc()}")
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Phase 2.5 E2E Validation ===")

    # 1. Fixture server
    fixture_server = start_fixture_server()
    time.sleep(0.2)
    test_r = httpx.get(f"{FIXTURE_BASE}/", timeout=3.0)
    if test_r.status_code != 200:
        log.error("Fixture server not serving: %d", test_r.status_code)
        sys.exit(1)
    log.info("Fixture server OK (%d bytes)", len(test_r.content))

    # 2. Seed all test data BEFORE backend starts (avoids pool contention)
    db_url = get_db_url()
    log.info("Seeding test data...")
    try:
        td = asyncio.run(_setup_all(db_url))
    except Exception as exc:
        log.error("DB seeding failed: %s\n%s", exc, traceback.format_exc())
        sys.exit(1)
    log.info("Seeded: user_id=%d, projects e2e1=%d e2e2=%d e2e3=%d e2e4=%d "
             "pag=%d exp=%d fp=%d fu=%d",
             td.user_id, td.e2e1_project_id, td.e2e2_project_id,
             td.e2e3_project_id, td.e2e4_project_id,
             td.pagination_project_id, td.export_project_id,
             td.failure_no_preview_id, td.failure_unconf_id)

    # 3. Start backend AFTER seeding
    log.info("Starting backend...")
    if not start_backend():
        stop_backend()
        sys.exit(1)

    # 4. Auth
    client = APIClient()
    if not client.login(TEST_EMAIL, TEST_PASSWORD):
        stop_backend()
        sys.exit(1)
    log.info("Logged in as %s", TEST_EMAIL)

    # 5. Scenarios
    scenarios: list[tuple[str, Any]] = [
        ("E2E-1", lambda: scenario_e2e_1(client, td)),
        ("E2E-2", lambda: scenario_e2e_2(client, td)),
        ("E2E-3", lambda: scenario_e2e_3(client, td)),
        ("E2E-4", lambda: scenario_e2e_4(client, td)),
        ("PAGINATION", lambda: scenario_pagination_api(client, td)),
        ("EXPORT", lambda: scenario_export(client, td)),
        ("RUN-SCOPING", lambda: scenario_run_scoping(client, td)),
        ("FAILURE_STATES", lambda: scenario_failure_states(client, td)),
        ("PROVIDER", lambda: scenario_provider(client)),
    ]

    for name, fn in scenarios:
        log.info("--- %s ---", name)
        try:
            result = fn()
            ALL_RESULTS.append(result)
            for line in result.evidence:
                log.info(line)
            for line in result.bugs:
                log.warning(line)
            log.info("%s: %s", name, "PASS" if result.passed else "FAIL")
        except Exception as exc:
            r_fail = ScenarioResult(name, passed=False)
            r_fail.bugs.append(f"{exc}\n{traceback.format_exc()}")
            ALL_RESULTS.append(r_fail)
            log.error("%s crashed: %s", name, exc)

    # 6. Summary
    passed = sum(1 for r in ALL_RESULTS if r.passed)
    total = len(ALL_RESULTS)
    print("\n" + "=" * 60)
    print(f"VALIDATION SUMMARY  {passed}/{total} PASSED")
    print("=" * 60)
    for r in ALL_RESULTS:
        icon = "PASS" if r.passed else "FAIL"
        print(f"\n[{icon}] {r.name}")
        for line in r.evidence:
            print(line.encode("ascii", "replace").decode("ascii"))
        for line in r.bugs:
            print(line.encode("ascii", "replace").decode("ascii"))

    # 7. Write JSON
    results_path = REPO_ROOT / "tests" / "validation" / "results.json"
    results_path.write_text(
        json.dumps(
            [{"name": r.name, "passed": r.passed, "evidence": r.evidence,
              "bugs": r.bugs, "fixes": r.fixes} for r in ALL_RESULTS],
            indent=2,
        )
    )
    log.info("Results written to %s", results_path)

    stop_backend()
    fixture_server.shutdown()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
