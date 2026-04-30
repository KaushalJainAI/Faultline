import subprocess
import time
import psutil
import os
import httpx
import logging

logger = logging.getLogger("Medic")

class Medic:
    def __init__(self, start_command, health_url=None, target_dir=None):
        self.start_command = start_command
        self.health_url = health_url
        self.target_dir = target_dir
        self.process = None

    def start_server(self):
        """Starts the target server process and waits for health check."""
        logger.info(f"Starting server with command: {self.start_command}")
        try:
            self.process = subprocess.Popen(
                self.start_command,
                shell=True,
                cwd=self.target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=None if os.name == 'nt' else os.setsid
            )
            logger.info(f"Server process launched (PID: {self.process.pid})")
            
            if not self.health_url:
                logger.warning("No health_url provided. Waiting 2 seconds for cold start.")
                time.sleep(2)
                return self.process.poll() is None

            # Poll health URL
            timeout = 30
            poll_interval = 1.0
            deadline = time.monotonic() + timeout
            
            logger.info(f"Polling health URL: {self.health_url} (timeout={timeout}s)")
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    _, stderr = self.process.communicate()
                    logger.error(f"Server process crashed during startup: {stderr.decode()}")
                    return False
                
                try:
                    with httpx.Client(timeout=2.0) as client:
                        response = client.get(self.health_url)
                        if response.status_code < 500:
                            logger.info("Server is healthy and ready.")
                            return True
                except (httpx.RequestError, httpx.HTTPStatusError):
                    pass
                
                time.sleep(poll_interval)
            
            logger.error("Timed out waiting for server health check.")
            return False
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            return False

    def is_alive(self):
        """Checks if the server process is still running."""
        if not self.process:
            return False
        
        # Check if process is still running
        if self.process.poll() is not None:
            return False

        # If health URL is provided, try to ping it
        if self.health_url:
            try:
                with httpx.Client(timeout=2.0) as client:
                    response = client.get(self.health_url)
                    return response.status_code < 500
            except Exception:
                return False
        
        return True

    def kill_server(self):
        """Forcefully kills the server and its children."""
        if not self.process:
            return

        logger.info(f"Killing server process and children (PID: {self.process.pid})")
        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            self.process.wait()
            logger.info("Server process killed successfully.")
        except psutil.NoSuchProcess:
            logger.warning("Process already dead.")
        except Exception as e:
            logger.error(f"Error killing server: {e}")

    def resurrect(self):
        """Kills the current server (if any) and restarts it."""
        logger.warning("Resurrection sequence initiated...")
        self.kill_server()
        time.sleep(2) # Give OS time to free ports
        return self.start_server()

    def monitor(self, interval=5):
        """Background monitoring loop (to be run in a thread or async)."""
        while True:
            if not self.is_alive():
                logger.error("Server health check failed!")
                self.resurrect()
            time.sleep(interval)
