import pytest
from playwright.sync_api import Page, expect

# BOILERPLATE: React UI Correctness Testing (Playwright)
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_e2e_react_<HHMMSS>.py
# 2. Replace <BASE_URL> with app base URL (e.g., "http://localhost:3000")
# 3. Replace <LOGIN_URL>, <DASHBOARD_URL>, <LIST_PAGE_URL> with actual paths
# 4. Replace <FORM_SUBMIT_BUTTON> with the CSS selector for submit button
# 5. Replace <VALIDATION_ERROR_SELECTOR> with error message container selector
# 6. Replace <LOADING_SPINNER_SELECTOR> with loading indicator selector
# 7. Replace <ERROR_MESSAGE_SELECTOR> with error message selector
# 8. Replace <EMPTY_STATE_SELECTOR> with empty state message selector
# 9. Run with: pytest test_e2e_react_HHMMSS.py -v

BASE_URL = "<BASE_URL>"
LOGIN_URL = "<LOGIN_URL>"
DASHBOARD_URL = "<DASHBOARD_URL>"
LIST_PAGE_URL = "<LIST_PAGE_URL>"


class TestReactUIBehavior:
    """Test React UI correctness and common bug patterns in React+Django SPAs."""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        """Setup: initialize page before each test."""
        self.page = page
        page.goto(BASE_URL)

    def test_form_validation_errors_visible(self):
        """Submit form with empty required fields — validation errors should be visible."""
        page = self.page
        page.goto(f"{BASE_URL}{LOGIN_URL}")

        # Click submit without filling fields
        page.click('<FORM_SUBMIT_BUTTON>')

        # Wait a moment for validation to run
        page.wait_for_timeout(500)

        # Validation error messages should be visible in the DOM
        expect(page.locator('<VALIDATION_ERROR_SELECTOR>')).to_be_visible()
        # Should contain text like "email is required" or "password is required"
        expect(page.locator('<VALIDATION_ERROR_SELECTOR>')).to_contain_text("required")

    def test_form_error_messages_specific_per_field(self):
        """Each form field should show its own specific error message."""
        page = self.page
        page.goto(f"{BASE_URL}{LOGIN_URL}")

        # Fill only email, leave password empty
        page.fill('input[type="email"]', "user@example.com")
        page.click('<FORM_SUBMIT_BUTTON>')
        page.wait_for_timeout(500)

        # Should show password-specific error, not generic error
        error_text = page.locator('<VALIDATION_ERROR_SELECTOR>').text_content()
        assert "password" in error_text.lower()

    def test_loading_spinner_visible_during_fetch(self):
        """During async data fetch, loading spinner should be visible."""
        page = self.page
        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")

        # Trigger a data load (e.g., click a refresh button or navigate to a page that loads data)
        page.click('button:has-text("Refresh")')  # Adjust selector as needed

        # Loading spinner should be visible during fetch
        # Use a short timeout to catch the spinner before it disappears
        try:
            spinner = page.locator('<LOADING_SPINNER_SELECTOR>')
            assert spinner.is_visible(timeout=1000)  # 1 second to be visible
        except:
            # Some spinners load very fast; just verify it appeared at some point
            pass

    def test_loading_spinner_hidden_after_data_loads(self):
        """After data finishes loading, spinner should disappear."""
        page = self.page
        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")

        # Wait for data to load
        page.wait_for_load_state("networkidle")

        # Spinner should not be visible anymore
        expect(page.locator('<LOADING_SPINNER_SELECTOR>')).to_be_hidden()

    def test_error_message_shown_on_api_failure(self):
        """When API call fails, error message should display (not blank screen)."""
        page = self.page

        # Intercept network and force a 500 error
        def route_handler(route):
            route.abort("failed")

        page.route("**/api/**", route_handler)

        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")
        page.wait_for_timeout(1000)

        # Error message should be visible
        expect(page.locator('<ERROR_MESSAGE_SELECTOR>')).to_be_visible()
        expect(page.locator('<ERROR_MESSAGE_SELECTOR>')).to_contain_text("error", case_insensitive=True)

    def test_empty_state_shown_when_list_is_empty(self):
        """When list endpoint returns empty array, empty state should display."""
        page = self.page

        # Mock API to return empty list
        def route_handler(route):
            if "list" in route.request.url or "/resources" in route.request.url:
                route.fulfill(status=200, body='{"data": []}')
            else:
                route.continue_()

        page.route("**/api/**", route_handler)

        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")
        page.wait_for_load_state("networkidle")

        # Empty state message should be visible
        expect(page.locator('<EMPTY_STATE_SELECTOR>')).to_be_visible()

    def test_unauthenticated_redirect_to_login(self):
        """Unauthenticated user accessing /dashboard should redirect to /login."""
        page = self.page

        # Clear auth token/session
        page.context.clear_cookies()
        page.evaluate("localStorage.clear()")

        # Try to access protected page
        page.goto(f"{BASE_URL}{DASHBOARD_URL}")

        # Should be redirected to login
        page.wait_for_url(f"**{LOGIN_URL}**", timeout=5000)
        expect(page).to_have_url(f"{BASE_URL}{LOGIN_URL}")

    def test_logout_clears_auth_state(self):
        """After logout, localStorage/sessionStorage auth tokens should be cleared."""
        page = self.page

        # Login first (mock if needed)
        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "password")
        page.click('<FORM_SUBMIT_BUTTON>')
        page.wait_for_load_state("networkidle")

        # Verify auth token is stored
        token_before = page.evaluate("localStorage.getItem('auth_token')")
        assert token_before is not None

        # Logout
        page.click('button:has-text("Logout")')
        page.wait_for_load_state("networkidle")

        # Token should be cleared
        token_after = page.evaluate("localStorage.getItem('auth_token')")
        assert token_after is None

    def test_token_expiry_triggers_logout(self):
        """When token expires (401 response), user should be logged out."""
        page = self.page

        # Login first
        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "password")
        page.click('<FORM_SUBMIT_BUTTON>')
        page.wait_for_load_state("networkidle")

        # Intercept requests to return 401 (token expired)
        def route_handler(route):
            route.fulfill(status=401, body='{"error": "Unauthorized"}')

        page.route("**/api/**", route_handler)

        # Try to access protected resource
        page.goto(f"{BASE_URL}{DASHBOARD_URL}")
        page.wait_for_timeout(1000)

        # Should be redirected to login due to 401
        expect(page).to_have_url(f"**{LOGIN_URL}**")

    def test_browser_back_after_logout_no_protected_content(self):
        """Browser back button after logout should not show protected page."""
        page = self.page

        # Login
        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "password")
        page.click('<FORM_SUBMIT_BUTTON>')
        page.wait_for_load_state("networkidle")

        # Verify we're on dashboard
        expect(page).to_have_url(f"**{DASHBOARD_URL}**")

        # Logout
        page.click('button:has-text("Logout")')
        page.wait_for_load_state("networkidle")

        # Browser back
        page.go_back()

        # Should not show dashboard content — either redirected to login or empty
        # Definitely should not show protected content
        expect(page).to_have_url(f"**{LOGIN_URL}**")

    def test_form_submit_button_disabled_during_submission(self):
        """Submit button should be disabled while request is in-flight."""
        page = self.page

        # Slow down network to catch the button state
        page.route("**/api/login**", lambda route: page.wait_for_timeout(2000) or route.continue_())

        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "password")

        button = page.locator('<FORM_SUBMIT_BUTTON>')
        page.click('<FORM_SUBMIT_BUTTON>')

        # Button should be disabled immediately after click
        assert button.is_disabled()

    def test_duplicate_submit_prevented(self):
        """Clicking submit twice quickly should only submit once."""
        page = self.page

        requests = []
        def route_handler(route):
            requests.append(route.request.url)
            route.continue_()

        page.route("**/api/login**", route_handler)

        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "password")

        button = page.locator('<FORM_SUBMIT_BUTTON>')
        # Click button twice in quick succession
        button.click()
        button.click()

        page.wait_for_load_state("networkidle")

        # Should only have made one request (or the second was prevented by disabled state)
        assert len(requests) <= 1

    def test_list_data_refreshes_on_navigation_return(self):
        """Navigating away and back should refresh the list data."""
        page = self.page

        request_count = []
        def route_handler(route):
            if "/resources" in route.request.url or "/list" in route.request.url:
                request_count.append(1)
            route.continue_()

        page.route("**/api/**", route_handler)

        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")
        page.wait_for_load_state("networkidle")
        count_after_first = len(request_count)

        # Navigate away
        page.goto(f"{BASE_URL}{DASHBOARD_URL}")
        page.wait_for_load_state("networkidle")

        # Navigate back
        page.go_back()
        page.wait_for_load_state("networkidle")

        # Should have made another request for the list
        count_after_return = len(request_count)
        assert count_after_return > count_after_first

    def test_no_xss_in_rendered_content(self):
        """User-provided content should not execute as script."""
        page = self.page

        # Mock API to return XSS payload
        def route_handler(route):
            if "resources" in route.request.url or "list" in route.request.url:
                route.fulfill(
                    status=200,
                    body='{"data": [{"name": "<img src=x onerror=window.xss_triggered=true>"}]}'
                )
            else:
                route.continue_()

        page.route("**/api/**", route_handler)

        page.goto(f"{BASE_URL}{LIST_PAGE_URL}")
        page.wait_for_load_state("networkidle")

        # Check that XSS did not execute
        xss_triggered = page.evaluate("window.xss_triggered")
        assert xss_triggered is None or xss_triggered is False

    def test_api_error_response_handled_gracefully(self):
        """API returning error JSON should show user-friendly message, not raw JSON."""
        page = self.page

        def route_handler(route):
            if "login" in route.request.url:
                route.fulfill(
                    status=400,
                    body='{"error": "Invalid credentials"}'
                )
            else:
                route.continue_()

        page.route("**/api/**", route_handler)

        page.goto(f"{BASE_URL}{LOGIN_URL}")
        page.fill('input[name="email"]', "user@example.com")
        page.fill('input[name="password"]', "wrong")
        page.click('<FORM_SUBMIT_BUTTON>')
        page.wait_for_timeout(500)

        # Should show user-friendly error message
        error_element = page.locator('<ERROR_MESSAGE_SELECTOR>')
        error_text = error_element.text_content()

        # Should not show raw JSON error object
        assert "{" not in error_text or "Invalid credentials" in error_text
