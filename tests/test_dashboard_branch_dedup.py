"""Tests for PRD prd-bd48bc5c: Fix duplicate branch entries in the repo branch selector.

Covers:
- Structural assertions that the guard (branchLoadSeq) is present in app.js and that
  applyRepo() no longer issues a redundant direct loadBranches() call (always run).
- Behavioral reproduction via a Node.js harness that forces two concurrent loadBranches()
  calls and asserts each branch appears exactly once (gated on `node` being available).
- TESTING_MODE widget injection (mirrors test_dashboard_browse_branches.py for this PRD).

All tests are hermetic: no live server, no real network, no hanging subprocess.
Gated on the optional dashboard extra (fastapi + httpx); self-skips in stdlib-only CI.
"""

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    import httpx  # noqa: F401
    import multipart  # noqa: F401
    from fastapi.testclient import TestClient

    from polis.dashboard.server import create_app
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

HAVE_NODE = shutil.which("node") is not None


# ---------------------------------------------------------------------------
# 1. Structural assertions — always run (gated on FastAPI only)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class BranchDedupAssetTest(unittest.TestCase):
    """Verify the guard against concurrent loads is present in the served JS asset."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-dedup-asset-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _get_js(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_branch_load_seq_guard_present(self):
        """state.branchLoadSeq counter and supersede check must exist in app.js."""
        js = self._get_js()
        self.assertIn("state.branchLoadSeq", js,
                      "state.branchLoadSeq counter missing from app.js")
        self.assertIn("seq !== state.branchLoadSeq", js,
                      "supersede-check 'seq !== state.branchLoadSeq' missing from app.js")

    def test_apply_repo_no_redundant_load_branches_call(self):
        """applyRepo() must call loadConfig() but must NOT directly call loadBranches()."""
        js = self._get_js()
        # Extract the applyRepo function body via brace matching
        marker = "async function applyRepo("
        start = js.find(marker)
        self.assertNotEqual(start, -1, "applyRepo function not found in app.js")
        # Walk forward to find the matching closing brace
        depth = 0
        body_start = js.index("{", start)
        i = body_start
        while i < len(js):
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = js[body_start:i + 1]
        self.assertIn("loadConfig(", body,
                      "applyRepo must still call loadConfig()")
        self.assertNotIn("loadBranches(", body,
                         "applyRepo must NOT directly call loadBranches() — that's the redundant call that caused duplication")

    def test_load_branches_function_signature_unchanged(self):
        """loadBranches(path) signature must still exist (keeps other tests green)."""
        js = self._get_js()
        self.assertIn("loadBranches(path)", js)


# ---------------------------------------------------------------------------
# 2. Behavioral reproduction — gated on Node.js
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
@unittest.skipUnless(HAVE_NODE, "node not available — behavioral test skipped")
class BranchDedupBehaviorTest(unittest.TestCase):
    """Force two concurrent loadBranches() calls via a Node harness and assert no duplicates."""

    PRD_BRANCHES = [
        "feat/dev-plan-then-execute",
        "feat/grounded-architect",
        "feat/grounded-reviewer",
        "feat/stable-dashboard-assets-and-restart",
        "fix/sync-main-before-branch",
        "main",
    ]

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-dedup-behavior-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_concurrent_load_branches_produces_no_duplicates(self):
        """Two overlapping loadBranches(path) calls must not double-append branches."""
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js_source = r.text

        # Extract just the loadBranches function body by brace-matching
        marker = "async function loadBranches("
        start = js_source.find(marker)
        self.assertNotEqual(start, -1, "loadBranches function not found in app.js")
        depth = 0
        body_start = js_source.index("{", start)
        i = body_start
        while i < len(js_source):
            if js_source[i] == "{":
                depth += 1
            elif js_source[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        load_branches_src = js_source[start:i + 1]

        branches_json = json.dumps(self.PRD_BRANCHES)

        # Node harness: stub the DOM and api(), then call loadBranches twice concurrently
        harness = textwrap.dedent(f"""\
            "use strict";
            // --- minimal DOM stub ---
            const fakeOptions = [{{value: "", textContent: "— branch —"}}];
            const fakeSelect = {{
              get innerHTML() {{ return ""; }},
              set innerHTML(v) {{
                // reset to placeholder only
                fakeOptions.length = 0;
                fakeOptions.push({{value: "", textContent: "— branch —"}});
              }},
              appendChild(opt) {{ fakeOptions.push({{value: opt.value, textContent: opt.textContent}}); }},
            }};
            const document = {{
              createElement(tag) {{ return {{value: "", textContent: ""}}; }},
              getElementById(id) {{ return fakeSelect; }},
            }};
            const $ = (id) => document.getElementById(id);

            // --- state stub (must match app.js structure) ---
            const state = {{ branchLoadSeq: 0 }};

            // --- api stub: resolves on next tick to FORCE overlap ---
            const BRANCHES = {branches_json};
            function api(method, path) {{
              return new Promise((resolve) => setImmediate(() => resolve({{branches: BRANCHES}})));
            }}

            // --- inject the real loadBranches implementation ---
            {load_branches_src}

            // --- run two concurrent calls ---
            async function main() {{
              const PATH = "/tmp/fake-repo";
              await Promise.all([loadBranches(PATH), loadBranches(PATH)]);
              // collect option values (skip placeholder "")
              const values = fakeOptions.map(o => o.value);
              console.log(JSON.stringify(values));
            }}
            main().catch(e => {{ console.error(e.message); process.exit(1); }});
        """)

        result = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0,
                         f"Node harness failed:\n{result.stderr}")

        options = json.loads(result.stdout.strip())
        # First entry is the placeholder ""
        self.assertEqual(options[0], "",
                         "First option must be the placeholder ''")
        branch_options = [v for v in options if v != ""]

        # Each branch must appear exactly once
        self.assertEqual(
            sorted(branch_options),
            sorted(self.PRD_BRANCHES),
            f"Expected {len(self.PRD_BRANCHES)} unique branches, got {len(branch_options)}: {branch_options}",
        )
        self.assertEqual(
            len(branch_options),
            len(self.PRD_BRANCHES),
            f"Duplicate branches detected: {branch_options}",
        )


# ---------------------------------------------------------------------------
# 3. TESTING_MODE widget injection (AC#7)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class WidgetInjectionTest(unittest.TestCase):
    """feedback-widget.js is injected iff testing_mode is on."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-widget-dedup-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_widget_present_when_testing_mode_on(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_widget_absent_when_testing_mode_off(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("feedback-widget.js", r.text)


if __name__ == "__main__":
    unittest.main()
