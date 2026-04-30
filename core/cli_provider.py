import subprocess
import shutil
import logging
import os
import shlex
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("CLIProvider")

class CLIAdapter:
    """Base class for all CLI-based model providers."""
    name: str = "base"
    binary: str = ""
    binary_env: str = ""

    def __init__(self, target_dir: str):
        self.target_dir = target_dir

    def is_installed(self) -> bool:
        return shutil.which(self.resolved_binary()) is not None

    def resolved_binary(self) -> str:
        return os.environ.get(self.binary_env, self.binary) if self.binary_env else self.binary

    def executable(self) -> str:
        return shutil.which(self.resolved_binary()) or self.resolved_binary()

    def get_auth_status(self) -> Tuple[bool, str]:
        raise NotImplementedError

    def run_task(self, prompt: str, options: Optional[Dict] = None) -> str:
        raise NotImplementedError

    def timeout(self, options: Optional[Dict], default: int) -> int:
        return options.get("timeout", default) if options else default

    def extra_args(self, env_name: str) -> list[str]:
        return shlex.split(os.environ.get(env_name, ""))

    def run_command(self, cmd: list[str], timeout: int) -> str:
        logger.info("Executing %s CLI: %s", self.name.title(), " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if result.returncode != 0:
                return f"{self.name.title()} CLI Error ({result.returncode}): {result.stderr or result.stdout}"
            return result.stdout
        except FileNotFoundError:
            resolved_cmd = [self.executable(), *cmd[1:]]
            result = subprocess.run(
                resolved_cmd,
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if result.returncode != 0:
                return f"{self.name.title()} CLI Error ({result.returncode}): {result.stderr or result.stdout}"
            return result.stdout
        except subprocess.TimeoutExpired:
            return f"Error: {self.name.title()} CLI task timed out."

class ClaudeAdapter(CLIAdapter):
    name = "claude"
    binary = "claude"
    binary_env = "FAULTLINE_CLAUDE_BINARY"

    def get_auth_status(self) -> Tuple[bool, str]:
        if not self.is_installed():
            return False, "Claude CLI Not Installed"
        try:
            result = subprocess.run([self.resolved_binary(), "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return True, (result.stdout or result.stderr or "Claude CLI detected").strip()
            return False, (result.stderr or "Claude CLI is installed but not usable").strip()
        except Exception:
            return True, "Claude CLI detected (auth status unknown)"

    def run_task(self, prompt: str, options: Optional[Dict] = None) -> str:
        cmd = [self.resolved_binary(), "-p", prompt, "--dangerously-skip-permissions", *self.extra_args("FAULTLINE_CLAUDE_CLI_ARGS")]
        return self.run_command(cmd, self.timeout(options, 600))

class GeminiAdapter(CLIAdapter):
    name = "gemini"
    binary = "gemini"
    binary_env = "FAULTLINE_GEMINI_BINARY"

    def get_auth_status(self) -> Tuple[bool, str]:
        if not self.is_installed():
            return False, "Gemini CLI Not Installed"
        return True, "Gemini CLI detected"

    def run_task(self, prompt: str, options: Optional[Dict] = None) -> str:
        cmd = [self.resolved_binary(), "-p", prompt, "--dangerously-skip-permissions", "--skip-trust", *self.extra_args("FAULTLINE_GEMINI_CLI_ARGS")]
        return self.run_command(cmd, self.timeout(options, 300))

class CodexAdapter(CLIAdapter):
    name = "codex"
    binary = "codex"
    binary_env = "FAULTLINE_CODEX_BINARY"

    def get_auth_status(self) -> Tuple[bool, str]:
        if not self.is_installed():
            return False, "Codex CLI Not Installed"
        try:
            result = subprocess.run([self.resolved_binary(), "login", "status"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return True, (result.stdout or "Codex CLI authenticated").strip()
            return False, (result.stderr or result.stdout or "Codex CLI is not authenticated").strip()
        except Exception:
            return True, "Codex CLI detected (auth status unknown)"

    def run_task(self, prompt: str, options: Optional[Dict] = None) -> str:
        cmd = [
            self.resolved_binary(),
            "exec",
            prompt,
            "--dangerously-skip-permissions",
            "--cd",
            self.target_dir,
            "--sandbox",
            os.environ.get("FAULTLINE_CODEX_SANDBOX", "read-only"),
            *self.extra_args("FAULTLINE_CODEX_CLI_ARGS"),
        ]
        return self.run_command(cmd, self.timeout(options, 600))

class ProviderManager:
    def __init__(self, target_dir: str):
        self.target_dir = target_dir
        self.adapters: Dict[str, CLIAdapter] = {
            "claude": ClaudeAdapter(target_dir),
            "gemini": GeminiAdapter(target_dir),
            "codex": CodexAdapter(target_dir),
        }

    def get_status(self) -> Dict[str, Any]:
        """Returns the installation and auth status of all providers."""
        status = {}
        for name, adapter in self.adapters.items():
            installed = adapter.is_installed()
            auth_ok, auth_msg = adapter.get_auth_status() if installed else (False, "Not installed")
            status[name] = {
                "installed": installed,
                "auth_ok": auth_ok,
                "message": auth_msg
            }
        return status

    def run(self, provider: str, prompt: str, options: Optional[Dict] = None) -> str:
        adapter = self.adapters.get(provider.lower())
        if not adapter:
            return f"Error: Provider {provider} not supported."
        
        status = self.get_status()
        if not status[provider.lower()]["installed"]:
            return f"Error: {provider} CLI is not installed. Please install it to use this provider."
        if not status[provider.lower()]["auth_ok"]:
            return f"Error: {provider} CLI is not authenticated: {status[provider.lower()]['message']}"
        
        return adapter.run_task(prompt, options)
