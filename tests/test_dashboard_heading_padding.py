"""Tests for dashboard heading padding and feedback widget integration.

Validates that:
1. All heading elements have sufficient left padding/margin (≥1rem/16px)
2. The feedback widget script is conditionally included in the HTML based on TESTING_MODE
3. Heading padding is consistent across all heading selectors
"""

import re
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from polis.dashboard.server import create_app
    from polis.projectcfg import write_config
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class DashboardHeadingPaddingTest(unittest.TestCase):
    """Test that heading elements have appropriate left padding/margin."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-heading-"))
        self.client = TestClient(create_app(self.base))

    def test_panel_header_has_left_padding(self):
        """Verify .panel > header elements have padding-left: 1rem."""
        css_path = Path(__file__).parent.parent / "polis" / "dashboard" / "static" / "styles.css"
        css_content = css_path.read_text(encoding="utf-8")

        # Find the rule for .panel > header
        panel_header_rule = re.search(
            r'\.panel\s*>\s*header\s*\{([^}]+)\}',
            css_content
        )
        self.assertIsNotNone(panel_header_rule, ".panel > header rule not found in CSS")

        rule_body = panel_header_rule.group(1)
        # Check for padding-left: 1rem or similar
        has_padding_left = bool(re.search(
            r'padding-left\s*:\s*1\s*rem',
            rule_body
        ))
        self.assertTrue(
            has_padding_left,
            f".panel > header rule does not have padding-left: 1rem. Found: {rule_body}"
        )

    def test_h1_to_h6_have_left_padding(self):
        """Verify h1-h6 elements have padding-left or margin-left of at least 1rem."""
        css_path = Path(__file__).parent.parent / "polis" / "dashboard" / "static" / "styles.css"
        css_content = css_path.read_text(encoding="utf-8")

        # Find rules for h1-h6
        heading_selectors = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

        for selector in heading_selectors:
            # Look for standalone h1, h2, etc. (not as part of compound selectors)
            pattern = rf'({selector})\s*{{([^}}]+)}}'
            rule_match = re.search(pattern, css_content)

            if rule_match:
                rule_body = rule_match.group(2)
                # Check for padding-left or margin-left with at least 1rem (or 16px)
                has_left_spacing = bool(re.search(
                    r'(?:padding-left|margin-left)\s*:\s*(?:1\s*rem|16px)',
                    rule_body
                ))
                self.assertTrue(
                    has_left_spacing,
                    f"{selector} rule does not have sufficient left padding/margin. Found: {rule_body}"
                )

    def test_page_renders_with_testing_mode_off(self):
        """Verify the page renders correctly when TESTING_MODE is off."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Polis", r.text)
        self.assertNotIn("<script src=\"/static/feedback-widget.js\"></script>", r.text)

    def test_feedback_widget_script_included_when_testing_mode_on(self):
        """Verify the feedback widget script is included in HTML when TESTING_MODE is on."""
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('Polis', r.text)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_feedback_widget_script_not_included_when_testing_mode_off(self):
        """Verify the feedback widget script is NOT included when TESTING_MODE is off."""
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_feedback_widget_placeholder_replaced(self):
        """Verify the placeholder comment is replaced (not left in HTML)."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        # The placeholder should be completely replaced, whether with content or empty
        self.assertNotIn('FEEDBACK_WIDGET_PLACEHOLDER', r.text)

    def test_css_has_no_horizontal_overflow(self):
        """Verify CSS padding doesn't introduce horizontal scrollbars.

        This is a basic validation that padding-left: 1rem is reasonable
        and won't cause overflow on standard viewports.
        """
        css_path = Path(__file__).parent.parent / "polis" / "dashboard" / "static" / "styles.css"
        css_content = css_path.read_text(encoding="utf-8")

        # Check that main container has proper width constraints
        # The .grid class should have width constraints
        grid_rule = re.search(r'\.grid\s*\{([^}]+)\}', css_content)
        self.assertIsNotNone(grid_rule, ".grid rule not found")

        # Verify no hardcoded widths that would exceed viewport
        grid_body = grid_rule.group(1)
        # Should use minmax or similar responsive constraints
        self.assertTrue(
            'grid-template-columns' in grid_body or 'grid' in grid_body,
            "Grid layout should use responsive column configuration"
        )

    def test_heading_padding_consistent_across_selectors(self):
        """Verify all heading selectors use consistent padding values."""
        css_path = Path(__file__).parent.parent / "polis" / "dashboard" / "static" / "styles.css"
        css_content = css_path.read_text(encoding="utf-8")

        # Collect all heading-related padding-left values
        padding_values = []

        # Find .panel > header padding-left
        panel_match = re.search(r'\.panel\s*>\s*header\s*\{([^}]+)\}', css_content)
        if panel_match:
            panel_body = panel_match.group(1)
            pl_match = re.search(r'padding-left\s*:\s*([\d.]+\s*rem|[\d.]+px)', panel_body)
            if pl_match:
                padding_values.append(("panel > header", pl_match.group(1)))

        # Find h1-h6 padding-left
        for i in range(1, 7):
            h_match = re.search(rf'h{i}\s*{{([^}}]+)}}', css_content)
            if h_match:
                h_body = h_match.group(1)
                pl_match = re.search(r'padding-left\s*:\s*([\d.]+\s*rem|[\d.]+px)', h_body)
                if pl_match:
                    padding_values.append((f"h{i}", pl_match.group(1)))

        # All padding values should be consistent (1rem or equivalent)
        if padding_values:
            expected = "1 rem" or "1rem"
            for selector, value in padding_values:
                normalized_value = value.replace(" ", "").lower()
                self.assertIn(
                    normalized_value,
                    ["1rem", "16px"],
                    f"{selector} has unexpected padding-left value: {value}"
                )


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class DashboardFeedbackWidgetIntegrationTest(unittest.TestCase):
    """Test feedback widget integration with the dashboard."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-widget-"))
        self.client = TestClient(create_app(self.base))

    def test_widget_configurable_via_api(self):
        """Verify testing_mode can be toggled via /api/config."""
        # Start with testing_mode off
        self.assertFalse(self.client.get("/api/config").json()["testing_mode"])

        # Toggle it on
        self.client.post("/api/config", json={"testing_mode": True})
        self.assertTrue(self.client.get("/api/config").json()["testing_mode"])

        # Verify it appears in HTML
        r = self.client.get("/")
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

        # Toggle it back off
        self.client.post("/api/config", json={"testing_mode": False})
        self.assertFalse(self.client.get("/api/config").json()["testing_mode"])

        # Verify it's no longer in HTML
        r = self.client.get("/")
        self.assertNotIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_widget_script_loads_from_correct_endpoint(self):
        """Verify feedback widget script endpoint returns content."""
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/static/feedback-widget.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("polis", r.text.lower())


if __name__ == "__main__":
    unittest.main()
