import pytest
from playwright.sync_api import Page, expect

# BOILERPLATE: End-to-End User Journey Test (Playwright)
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_e2e_journey_<HHMMSS>.py
# 2. Replace <BASE_URL> with the app base URL (e.g., "http://localhost:3000")
# 3. Replace <REGISTER_URL> with registration page path (e.g., "/register")
# 4. Replace <LOGIN_URL> with login page path (e.g., "/login")
# 5. Replace <DASHBOARD_URL> with authenticated dashboard path (e.g., "/dashboard")
# 6. Replace <RESOURCE_LIST_URL> with resource list page (e.g., "/resources")
# 7. Replace <USERNAME>, <PASSWORD> with test credentials
# 8. Replace selector placeholders with actual CSS/XPath selectors from your app
# 9. Run with: pytest test_e2e_journey_HHMMSS.py -v
#    (requires Playwright browsers installed: playwright install)

BASE_URL = "<BASE_URL>"


class TestUserJourney:
    """Test a complete user journey from registration through logout."""

    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        """Setup: navigate to base URL before each test."""
        self.page = page
        page.goto(BASE_URL)

    def test_user_registration_flow(self):
        """Register a new user and verify redirect to dashboard or login."""
        page = self.page
        page.goto(f"{BASE_URL}<REGISTER_URL>")

        # Assume registration form has: email, password, password_confirm, submit button
        page.fill('input[name="email"]', "testuser@example.com")  # Adjust selector
        page.fill('input[name="password"]', "<PASSWORD>")
        page.fill('input[name="password_confirm"]', "<PASSWORD>")

        # Submit
        page.click('button[type="submit"]')  # Adjust selector

        # Verify success: either redirect to login or dashboard
        # Option 1: Check URL changed
        page.wait_for_url(f"{BASE_URL}/<LOGIN_URL>", timeout=5000)  # or /dashboard
        # Option 2: Check success message appears
        # expect(page.locator(".success-message")).to_contain_text("registered successfully")

    def test_user_login_flow(self):
        """Login with registered credentials and verify access to dashboard."""
        page = self.page
        page.goto(f"{BASE_URL}<LOGIN_URL>")

        # Fill login form
        page.fill('input[name="email"]', "<USERNAME>")  # Adjust selector
        page.fill('input[name="password"]', "<PASSWORD>")

        # Submit
        page.click('button[type="submit"]')  # Adjust selector

        # Verify redirect to dashboard
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>", timeout=5000)

        # Verify dashboard is visible
        expect(page.locator("h1")).to_contain_text("Dashboard")  # Adjust selector

    def test_create_resource_via_ui(self):
        """Login, navigate to resource list, create a new resource."""
        page = self.page

        # Login first
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "<USERNAME>")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        # Navigate to resource list
        page.goto(f"{BASE_URL}<RESOURCE_LIST_URL>")

        # Click "Create" or "Add" button
        page.click('<CREATE_BUTTON_SELECTOR>')  # e.g., 'button:has-text("Create")'

        # Fill in resource form
        page.fill('input[name="name"]', "Test Resource")  # Adjust selector
        page.fill('textarea[name="description"]', "A test resource created by E2E test")

        # Submit
        page.click('button[type="submit"]')

        # Verify the resource appears in the list
        expect(page.locator("body")).to_contain_text("Test Resource")
        # Or check for success toast/message
        expect(page.locator(".toast, .alert")).to_contain_text("created successfully")

    def test_edit_resource_via_ui(self):
        """Create a resource, edit it, and verify changes."""
        page = self.page

        # Login and navigate to list
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "<USERNAME>")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        page.goto(f"{BASE_URL}<RESOURCE_LIST_URL>")

        # Assuming there's an "Edit" button or link per resource
        page.click('<EDIT_BUTTON_SELECTOR>')  # e.g., first row edit button

        # Update fields
        page.fill('input[name="name"]', "Updated Resource Name")
        page.click('button[type="submit"]')

        # Verify update succeeded
        expect(page.locator("body")).to_contain_text("Updated Resource Name")
        expect(page.locator(".toast")).to_contain_text("updated successfully")

    def test_delete_resource_via_ui(self):
        """Create a resource, delete it, and verify it's gone from the list."""
        page = self.page

        # Login and navigate to list
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "<USERNAME>")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        page.goto(f"{BASE_URL}<RESOURCE_LIST_URL>")

        # Create a resource first
        page.click('<CREATE_BUTTON_SELECTOR>')
        page.fill('input[name="name"]', "Resource to Delete")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Find and click delete button
        page.click('<DELETE_BUTTON_SELECTOR>')  # e.g., button next to the resource

        # Confirm deletion if there's a confirmation dialog
        if page.locator("button:has-text('Confirm')").is_visible():
            page.click("button:has-text('Confirm')")

        # Verify resource is gone
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).not_to_contain_text("Resource to Delete")

    def test_logout_flow(self):
        """Login, then logout and verify redirect to login page."""
        page = self.page

        # Login
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "<USERNAME>")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        # Click logout
        page.click('<LOGOUT_BUTTON_SELECTOR>')  # e.g., 'button:has-text("Logout")'

        # Verify redirect to login
        page.wait_for_url(f"{BASE_URL}<LOGIN_URL>", timeout=5000)

    def test_protected_page_without_auth(self):
        """Navigate to dashboard without logging in — should redirect to login."""
        page = self.page

        # Try to access dashboard directly without auth
        page.goto(f"{BASE_URL}<DASHBOARD_URL>")

        # Should be redirected to login
        page.wait_for_url(f"{BASE_URL}<LOGIN_URL>", timeout=5000)

    def test_browser_back_after_logout_protected(self):
        """After logout, using browser back button should not show protected content."""
        page = self.page

        # Login
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "<USERNAME>")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        # Logout
        page.click('<LOGOUT_BUTTON_SELECTOR>')
        page.wait_for_url(f"{BASE_URL}<LOGIN_URL>")

        # Press browser back button
        page.go_back()

        # Should be redirected back to login, not show dashboard
        expect(page).to_have_url(f"{BASE_URL}<LOGIN_URL>")

    def test_complete_crud_journey(self):
        """Full journey: register → login → create → edit → delete → logout."""
        page = self.page

        # Register
        page.goto(f"{BASE_URL}<REGISTER_URL>")
        page.fill('input[name="email"]', "newuser@example.com")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.fill('input[name="password_confirm"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}.*", timeout=5000)

        # Login with new account
        page.goto(f"{BASE_URL}<LOGIN_URL>")
        page.fill('input[name="email"]', "newuser@example.com")
        page.fill('input[name="password"]', "<PASSWORD>")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{BASE_URL}<DASHBOARD_URL>")

        # Create resource
        page.goto(f"{BASE_URL}<RESOURCE_LIST_URL>")
        page.click('<CREATE_BUTTON_SELECTOR>')
        page.fill('input[name="name"]', "Journey Test Resource")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Edit resource
        page.click('<EDIT_BUTTON_SELECTOR>')
        page.fill('input[name="name"]', "Updated Journey Resource")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Delete resource
        page.click('<DELETE_BUTTON_SELECTOR>')
        if page.locator("button:has-text('Confirm')").is_visible():
            page.click("button:has-text('Confirm')")
        page.wait_for_load_state("networkidle")

        # Logout
        page.click('<LOGOUT_BUTTON_SELECTOR>')
        page.wait_for_url(f"{BASE_URL}<LOGIN_URL>")
