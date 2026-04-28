# Faultline Skills (The Tool Library)

Skills are independent Python services used by the Aegis-Breaker agent to interact with the target application and the host operating system.

## 🩺 The Medic (`medic.py`)
Responsible for the lifecycle of the target application.
- **Monitoring**: Pings a health URL or checks the PID status.
- **Resurrection**: If the server crashes during an attack, the Medic kills orphaned processes and restarts the server using the `start_command`.
- **Recursive Kill**: Uses `psutil` to ensure child processes (like Gunicorn workers) are cleaned up.

## 🗺️ The Cartographer (`ast_grapher.py`)
Provides structural awareness to the AI.
- **Static Analysis**: Uses the `ast` module to map classes, functions, and imports without executing code.
- **Dependency Mapping**: Identifies which modules depend on each other to help the agent calculate the "blast radius" of an attack.

## 🎯 The Siege Engine (`attacker.py`)
The heavy-artillery of the platform.
- **Asynchronous Bursting**: Uses `httpx` and `asyncio` to send high volumes of concurrent requests.
- **Tracing**: Injects `X-Aegis-Request-ID` headers into every request, allowing the Coroner to link crashes back to specific payloads.

## 🩸 The Coroner (`log_correlator.py`)
Monitors the target's heartbeat (logs).
- **Log Tailing**: Uses `watchdog` to monitor file system events on log files.
- **Crash Correlation**: Scans for tracebacks and matches them to the `X-Aegis-Request-ID` captured in the log stream.

## 🛡️ Guardrail Validator (`guardrails.py`)
Prevents AI hallucinations.
- **Import Verification**: Checks that any modules the AI tries to use in its test scripts actually exist in the target's environment.
- **Signature Checking**: Ensures that any mocked functions match the real backend signatures.
- **Linting**: Runs `ruff` to catch syntax or logic errors in AI-generated code.
