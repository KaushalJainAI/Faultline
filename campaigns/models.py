import uuid
from django.db import models


class Campaign(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    target_path = models.TextField()
    target_url = models.URLField()
    start_command = models.TextField()
    health_url = models.URLField(blank=True, null=True)
    log_file = models.TextField(default="server.log")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True)
    report_path = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.id} ({self.status})"


class Finding(models.Model):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Category(models.TextChoices):
        SYNTAX = "syntax", "Syntax"
        SEMANTIC = "semantic", "Semantic"
        RUNTIME = "runtime", "Runtime"
        API = "api", "API"
        SECURITY_CANDIDATE = "security_candidate", "Security Candidate"

    campaign = models.ForeignKey(Campaign, related_name="findings", on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=40, choices=Category.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=40, default="open")
    summary = models.TextField(blank=True)
    evidence = models.TextField(blank=True)
    reproduction_steps = models.TextField(blank=True)
    suggested_fix = models.TextField(blank=True)
    file_path = models.TextField(blank=True)
    line_number = models.PositiveIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ToolRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        ERROR = "error", "Error"

    campaign = models.ForeignKey(Campaign, related_name="tool_runs", on_delete=models.CASCADE)
    tool_name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    input_summary = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["started_at"]

    def __str__(self):
        return f"{self.tool_name} ({self.status})"
