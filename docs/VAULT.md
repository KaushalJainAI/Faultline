# Vault Authentication System

Faultline's "Vault" is a dynamic authentication management system designed to handle session credentials when testing target applications that require authorization. 

The Vault allows you to configure authentication workflows (called `AuthFlows`) that run before the Aegis-Breaker agent starts exploring the target. The acquired credentials are then automatically injected into all subsequent HTTP requests made by the agent's testing tools (like the Attacker or functional test runners).

## Understanding AuthFlows

An `AuthFlow` defines how Faultline should obtain and inject credentials. There are two primary types of authentication flows supported:

### 1. Static Token (`static`)
Use this mode when you already have a long-lived API key, a predefined bearer token, or a specific session cookie that you want Faultline to use. 

- **No login request is made.**
- Faultline simply takes the token you provide and injects it into every request.

### 2. Login Endpoint (`login`)
Use this mode when Faultline needs to dynamically authenticate with the target application before testing. Faultline will act like a user (or API client) and submit credentials to a specific endpoint, parse the response, and extract the session token.

- **A login request is made.** You must provide the target endpoint URL, the HTTP method (usually `POST`), and the JSON payload containing credentials (e.g., email and password).
- Faultline will read the response, extract the token based on a JSON path you provide, and use it for subsequent requests.

## Token Extraction

When using the `login` type, the target server will return the session token in the response body. Faultline needs to know where to find it. You configure this using the `token_extraction_path`.

This path uses dot notation to traverse JSON objects.

**Examples:**
- If the response is `{"access_token": "ey123..."}`, the extraction path is `access_token`.
- If the response is `{"data": {"session": {"token": "ey123..."}}}`, the extraction path is `data.session.token`.

## Token Injection

Once Faultline has a token (either statically provided or dynamically extracted), it needs to know how to include it in requests sent to the target application.

You configure three properties for injection:
1. **Injection Type**: Either `header` or `cookie`.
2. **Injection Key**: The name of the header or cookie (e.g., `Authorization`, `X-API-Key`, `sessionid`).
3. **Injection Format**: The format of the value. Use `{token}` as a placeholder for the actual token string.
   - For a standard bearer token, use: `Bearer {token}`
   - For a raw token, simply use: `{token}`

## Example Configurations

You can configure AuthFlows via the Faultline Django Admin interface, or through the REST API (`POST /api/v1/vault/auth-flows/`).

### Example 1: Static API Key in Header

```json
{
  "name": "Production API Key",
  "auth_type": "static",
  "auth_payload": "my-super-secret-api-key-123",
  "injection_type": "header",
  "injection_key": "X-API-Key",
  "injection_format": "{token}"
}
```

### Example 2: Dynamic JWT Login

This flow posts credentials to `/api/login`, extracts the `access_token` from the JSON response, and injects it as a `Bearer` token in the `Authorization` header.

```json
{
  "name": "Standard User Login",
  "auth_type": "login",
  "auth_url": "/api/login",
  "auth_method": "POST",
  "auth_payload": {
    "username": "testuser@example.com",
    "password": "securepassword123"
  },
  "token_extraction_path": "access_token",
  "injection_type": "header",
  "injection_key": "Authorization",
  "injection_format": "Bearer {token}"
}
```

### Example 3: Dynamic Cookie Login

This flow logs in and expects a token inside a nested JSON structure `{"auth": {"token": "..."}}`. It injects this token into a cookie named `session_id`.

```json
{
  "name": "Admin Portal Login",
  "auth_type": "login",
  "auth_url": "/auth/admin",
  "auth_method": "POST",
  "auth_payload": {
    "username": "admin",
    "password": "adminpassword"
  },
  "token_extraction_path": "auth.token",
  "injection_type": "cookie",
  "injection_key": "session_id",
  "injection_format": "{token}"
}
```

## Running a Campaign with an AuthFlow

When starting a campaign via the REST API (`POST /api/v1/campaign/start/`), you can optionally include the `auth_flow` ID. 

Faultline will execute the specified AuthFlow before initializing the LangGraph agent, ensuring the agent's tools have the correct session headers.

```json
{
  "target_path": "C:/path/to/project",
  "target_url": "http://127.0.0.1:8000",
  "start_command": "python manage.py runserver",
  "auth_flow": "123e4567-e89b-12d3-a456-426614174000"
}
```
