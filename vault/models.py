from django.db import models
import uuid

class AuthFlow(models.Model):
    class AuthType(models.TextChoices):
        STATIC_TOKEN = "static", "Static Token"
        LOGIN_ENDPOINT = "login", "Login Endpoint"

    class InjectionType(models.TextChoices):
        HEADER = "header", "Header"
        COOKIE = "cookie", "Cookie"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    auth_type = models.CharField(max_length=20, choices=AuthType.choices, default=AuthType.LOGIN_ENDPOINT)
    
    # Required for LOGIN_ENDPOINT
    auth_url = models.CharField(max_length=512, blank=True, help_text="Relative or absolute URL for authentication (e.g. /api/login)")
    auth_method = models.CharField(max_length=10, default="POST")
    auth_payload = models.JSONField(blank=True, null=True, help_text="JSON payload to send (e.g. email/password). If STATIC_TOKEN, this can just be the token string.")
    token_extraction_path = models.CharField(max_length=255, blank=True, help_text="JSON path or simple key to extract the token from the response (e.g. 'access_token' or 'data.token')")
    
    # Injection details
    injection_type = models.CharField(max_length=20, choices=InjectionType.choices, default=InjectionType.HEADER)
    injection_key = models.CharField(max_length=120, default="Authorization", help_text="e.g. 'Authorization' or 'session_id'")
    injection_format = models.CharField(max_length=255, default="Bearer {token}", help_text="Format string. Use {token} as placeholder.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
