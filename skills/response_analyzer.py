"""
skills/response_analyzer.py

Post-processor for SiegeEngine assault results. Detects subtle vulnerabilities
beyond simple HTTP 500 errors: info leaks, auth bypasses, timing anomalies,
and sensitive data exposure.

Used automatically after execute_assault() and surfaced to the agent
as structured anomaly data.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger("ResponseAnalyzer")

# --- Detection patterns -----------------------------------------------------------

_INFO_LEAK_PATTERNS = re.compile(
    r"(Traceback \(most recent call last\)|"
    r"at [\w./]+\.py:\d+|"
    r"File \"[^\"]+\", line \d+|"
    r"django\.db\.utils\.|"
    r"psycopg2\.|"
    r"sqlalchemy\.exc\.|"
    r"OperationalError|ProgrammingError|"
    r"SQLSTATE|syntax error at or near|"
    r"DEBUG\s*=\s*True|"
    r"SECRET_KEY|"
    r"<pre class=\"exception_value\">)",
    re.IGNORECASE,
)

_SENSITIVE_DATA_PATTERNS = re.compile(
    r"(password|passwd|secret|token|api_key|"
    r"private_key|ssn|credit.?card|"
    r"-----BEGIN (?:RSA |EC )?PRIVATE KEY)",
    re.IGNORECASE,
)

_STACK_TRACE_PATTERN = re.compile(
    r"(Traceback|at \w+\.\w+\(|Exception in thread|"
    r"caused by:|java\.lang\.|"
    r"File \"[^\"]+\", line \d+, in \w+)",
    re.IGNORECASE,
)


# --- Anomaly classification -------------------------------------------------------

def _classify_anomaly(
    result: Dict,
    baseline_avg_ms: Optional[float] = None,
) -> Optional[Dict]:
    """
    Analyze a single SiegeEngine result for anomalies.
    Returns an anomaly dict or None if clean.
    """
    status = result.get("status_code")
    text = result.get("response_text", "") or ""
    payload = result.get("payload", {})
    endpoint = result.get("endpoint", "")

    anomalies = []

    # 1. Information leak — stack traces / debug output in response body
    if _INFO_LEAK_PATTERNS.search(text):
        anomalies.append({
            "type": "info_leak",
            "severity": "high",
            "detail": "Response contains stack trace or debug information",
            "evidence": _extract_evidence(text, _INFO_LEAK_PATTERNS),
        })

    # 2. Sensitive data exposure
    if _SENSITIVE_DATA_PATTERNS.search(text) and status and 200 <= status < 300:
        anomalies.append({
            "type": "sensitive_data_exposure",
            "severity": "critical",
            "detail": "Response may contain sensitive data (passwords, keys, tokens)",
            "evidence": _extract_evidence(text, _SENSITIVE_DATA_PATTERNS),
        })

    # 3. Auth bypass — 200 on a request that was tagged as an auth-bypass attempt
    attack_type = payload.get("_attack_type", "") if isinstance(payload, dict) else ""
    if status and 200 <= status < 300 and "auth_bypass" in str(attack_type).lower():
        anomalies.append({
            "type": "auth_bypass",
            "severity": "critical",
            "detail": f"Endpoint {endpoint} returned {status} on auth-bypass payload",
        })

    # 4. Privilege escalation — 200 on admin endpoint with normal user token
    if status and 200 <= status < 300 and "privilege" in str(attack_type).lower():
        anomalies.append({
            "type": "privilege_escalation",
            "severity": "critical",
            "detail": f"Endpoint {endpoint} returned {status} on privilege escalation attempt",
        })

    # 5. Verbose error — 4xx/5xx that leaks internal details
    if status and status >= 400 and _STACK_TRACE_PATTERN.search(text):
        anomalies.append({
            "type": "verbose_error",
            "severity": "medium",
            "detail": f"Error response ({status}) contains internal stack trace",
            "evidence": _extract_evidence(text, _STACK_TRACE_PATTERN),
        })

    # 6. Unexpected success — 200 on an injection payload
    if status and 200 <= status < 300 and _is_injection_payload(payload):
        anomalies.append({
            "type": "injection_success",
            "severity": "high",
            "detail": f"Injection payload accepted with {status} — investigate for actual exploitation",
        })

    # 7. Rate Limiting — 429 Too Many Requests
    if status == 429:
        anomalies.append({
            "type": "rate_limit_hit",
            "severity": "low",
            "detail": f"Endpoint {endpoint} returned 429 Too Many Requests — campaign may be throttled",
        })

    if not anomalies:
        return None

    return {
        **result,
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "max_severity": _max_severity(anomalies),
    }


def _extract_evidence(text: str, pattern: re.Pattern, context_chars: int = 120) -> str:
    """Extract a short evidence snippet around the first match."""
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - 30)
    end = min(len(text), match.end() + context_chars)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _is_injection_payload(payload: Dict) -> bool:
    """Check if any payload value contains common injection strings."""
    if not isinstance(payload, dict):
        return False
    injection_markers = ["' OR ", "1=1", "DROP TABLE", "UNION SELECT",
                         "<script>", "{{", "${", "; ls", "| cat"]
    payload_str = str(payload).lower()
    return any(marker.lower() in payload_str for marker in injection_markers)


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _max_severity(anomalies: List[Dict]) -> str:
    """Return the highest severity among a list of anomalies."""
    best = "low"
    for a in anomalies:
        sev = a.get("severity", "low")
        if _SEVERITY_RANK.get(sev, 99) < _SEVERITY_RANK.get(best, 99):
            best = sev
    return best


# --- Public API -------------------------------------------------------------------

def analyze_assault_results(results: List[Dict]) -> Dict:
    """
    Analyze a full SiegeEngine assault result set for anomalies.

    Returns:
        {
            "total_requests": int,
            "anomaly_count": int,
            "anomalies": [...],
            "summary": str,
            "severity_distribution": {"critical": N, "high": N, ...}
        }
    """
    anomalies = []
    severity_dist = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for result in results:
        anomaly = _classify_anomaly(result)
        if anomaly:
            anomalies.append(anomaly)
            sev = anomaly.get("max_severity", "medium")
            severity_dist[sev] = severity_dist.get(sev, 0) + 1

    # Build human-readable summary
    if not anomalies:
        summary = f"No anomalies detected across {len(results)} requests."
    else:
        lines = [f"⚠️  {len(anomalies)} anomalies detected across {len(results)} requests:"]
        for sev in ["critical", "high", "medium", "low"]:
            count = severity_dist.get(sev, 0)
            if count > 0:
                lines.append(f"  {sev.upper()}: {count}")
        summary = "\n".join(lines)

    return {
        "total_requests": len(results),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "severity_distribution": severity_dist,
        "summary": summary,
    }
