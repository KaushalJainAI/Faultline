from locust import HttpUser, TaskSet, task, between, events
import logging

# BOILERPLATE: Locust Load Testing Template
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_load_<HHMMSS>.py
# 2. Replace <BASE_HOST> with the target hostname (e.g., "http://localhost:8000")
# 3. Replace <LOGIN_ENDPOINT> with login endpoint (e.g., "/api/v1/auth/login")
# 4. Replace <LOGIN_PAYLOAD> with sample login credentials
# 5. Replace <TOKEN_JSON_PATH> with the response field containing the token (e.g., "token" or "data.access_token")
# 6. Replace <LIST_ENDPOINT> with a read endpoint (e.g., "/api/v1/resources")
# 7. Replace <DETAIL_ENDPOINT> with a detail endpoint (e.g., "/api/v1/resources/{id}")
# 8. Replace <CREATE_ENDPOINT> with a create endpoint (e.g., "/api/v1/resources")
# 9. Run with: locust -f test_load_HHMMSS.py --host=<BASE_HOST> -u 100 -r 10 -t 5m
#    (100 users, 10 users per second spawn rate, 5 minute duration)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def get_nested_value(obj, path):
    """Helper to extract nested JSON values. E.g., 'data.access_token' -> obj['data']['access_token']"""
    keys = path.split('.')
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


class ReadTask(TaskSet):
    """Read-heavy workload: LIST and GET operations."""

    def on_start(self):
        """Authenticate before running tasks."""
        self.token = None
        self.resource_id = 1  # Default; will be updated from list response
        self.login()

    def login(self):
        """Login and store auth token."""
        payload = {
            # <LOGIN_PAYLOAD>
        }
        response = self.client.post(
            "<LOGIN_ENDPOINT>",
            json=payload,
            catch_response=True
        )
        if response.status_code == 200:
            try:
                token = get_nested_value(response.json(), "<TOKEN_JSON_PATH>")
                if token:
                    self.token = token
                    response.success()
                else:
                    response.failure(f"Token not found in response")
            except Exception as e:
                response.failure(f"Failed to extract token: {e}")
        else:
            response.failure(f"Login failed with status {response.status_code}")

    @task(3)
    def list_resources(self):
        """GET list of resources — high frequency."""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        with self.client.get(
            "<LIST_ENDPOINT>",
            headers=headers,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    # Extract an ID from response for detail requests
                    if isinstance(data, list) and len(data) > 0:
                        self.resource_id = data[0].get("id", 1)
                    elif isinstance(data, dict) and "data" in data:
                        items = data["data"]
                        if isinstance(items, list) and len(items) > 0:
                            self.resource_id = items[0].get("id", 1)
                    response.success()
                except Exception as e:
                    response.failure(f"Failed to parse response: {e}")
            else:
                response.failure(f"GET list failed with {response.status_code}")

    @task(1)
    def get_detail(self):
        """GET detail of a resource."""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        detail_url = f"<DETAIL_ENDPOINT>".replace("{id}", str(self.resource_id))
        with self.client.get(
            detail_url,
            headers=headers,
            catch_response=True
        ) as response:
            if response.status_code in [200, 404]:  # 404 is ok, resource might not exist
                response.success()
            else:
                response.failure(f"GET detail failed with {response.status_code}")

    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks


class WriteTask(TaskSet):
    """Write-heavy workload: POST and DELETE operations."""

    def on_start(self):
        """Authenticate before running tasks."""
        self.token = None
        self.created_id = None
        self.login()

    def login(self):
        """Login and store auth token."""
        payload = {
            # <LOGIN_PAYLOAD>
        }
        response = self.client.post(
            "<LOGIN_ENDPOINT>",
            json=payload,
            catch_response=True
        )
        if response.status_code == 200:
            try:
                token = get_nested_value(response.json(), "<TOKEN_JSON_PATH>")
                if token:
                    self.token = token
                    response.success()
                else:
                    response.failure("Token not found in response")
            except Exception as e:
                response.failure(f"Failed to extract token: {e}")
        else:
            response.failure(f"Login failed with status {response.status_code}")

    @task(2)
    def create_resource(self):
        """POST to create a resource."""
        payload = {
            # <CREATE_PAYLOAD>
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        with self.client.post(
            "<CREATE_ENDPOINT>",
            json=payload,
            headers=headers,
            catch_response=True
        ) as response:
            if response.status_code in [200, 201]:
                try:
                    created = response.json()
                    self.created_id = created.get("id")
                    response.success()
                except Exception as e:
                    response.failure(f"Failed to parse response: {e}")
            else:
                response.failure(f"POST create failed with {response.status_code}")

    @task(1)
    def delete_resource(self):
        """DELETE a resource (the one we just created, or a known ID)."""
        if not self.created_id:
            # Skip if we haven't successfully created one yet
            return

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        delete_url = f"<DETAIL_ENDPOINT>".replace("{id}", str(self.created_id))
        with self.client.delete(
            delete_url,
            headers=headers,
            catch_response=True
        ) as response:
            if response.status_code in [200, 204, 404]:
                response.success()
                self.created_id = None  # Reset after delete
            else:
                response.failure(f"DELETE failed with {response.status_code}")

    wait_time = between(2, 4)  # Longer wait for write operations


class AuthTaskSet(TaskSet):
    """Auth workload: high concurrent login attempts."""

    @task
    def login_attempt(self):
        """POST login request."""
        payload = {
            # <LOGIN_PAYLOAD>
        }
        with self.client.post(
            "<LOGIN_ENDPOINT>",
            json=payload,
            catch_response=True
        ) as response:
            if response.status_code in [200, 401, 400]:  # Any of these is acceptable
                response.success()
            else:
                response.failure(f"Login returned unexpected {response.status_code}")

    wait_time = between(0.5, 1.5)  # Quick attempts


class ReadHeavyUser(HttpUser):
    """User that mainly reads data (LIST + GET)."""
    tasks = [ReadTask]
    wait_time = between(1, 3)


class WriteHeavyUser(HttpUser):
    """User that mainly writes data (POST + DELETE)."""
    tasks = [WriteTask]
    wait_time = between(2, 5)


class AuthUser(HttpUser):
    """User that authenticates repeatedly under load."""
    tasks = [AuthTaskSet]
    wait_time = between(0.5, 2)


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """
    Event listener: check failure ratio and exit with error code if too many failures.
    """
    if environment.stats.total.fail_ratio > 0.01:  # More than 1% failure rate
        log.error(f"Test failed: failure ratio {environment.stats.total.fail_ratio} > 1%")
        environment.process_exit_code = 1
    else:
        log.info(f"Test passed: failure ratio {environment.stats.total.fail_ratio} <= 1%")


# Optional: print summary stats at the end
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print performance summary."""
    print("\n--- Load Test Summary ---")
    print(f"Total Requests: {environment.stats.total.num_requests}")
    print(f"Total Failures: {environment.stats.total.num_failures}")
    print(f"Failure Rate: {environment.stats.total.fail_ratio * 100:.2f}%")
    print(f"Median Response Time: {environment.stats.total.median_response_time}ms")
    print(f"95th Percentile: {environment.stats.total.get_percentile(0.95)}ms")
    print(f"99th Percentile: {environment.stats.total.get_percentile(0.99)}ms")
    print(f"Max Response Time: {environment.stats.total.max_response_time}ms")
