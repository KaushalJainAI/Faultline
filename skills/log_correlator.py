import time
import re
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Coroner")

class LogFileHandler(FileSystemEventHandler):
    def __init__(self, target_file, callback):
        self.target_file = target_file
        self.callback = callback
        self._file = open(self.target_file, 'r', encoding='utf-8', errors='ignore')
        # Seek to the end of file
        self._file.seek(0, 2)

    def on_modified(self, event):
        if event.src_path == self.target_file:
            lines = self._file.readlines()
            for line in lines:
                self.callback(line)

class LogCorrelator:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self.observer = Observer()
        self.crashes = {}

    def _process_log_line(self, line: str):
        # Extremely basic regex to find our injected Chaos ID in the logs
        # Assumes the target application logs headers or we can identify the context
        chaos_id_match = re.search(r'X-Aegis-Request-ID[:=]?\s*([a-f0-9\-]+)', line)
        error_match = re.search(r'(ERROR|Exception|Traceback)', line, re.IGNORECASE)

        if error_match:
            logger.warning(f"Error caught in log: {line.strip()}")
            if chaos_id_match:
                request_id = chaos_id_match.group(1)
                if request_id not in self.crashes:
                    self.crashes[request_id] = []
                self.crashes[request_id].append(line.strip())

    def start_watching(self):
        try:
            event_handler = LogFileHandler(self.log_path, self._process_log_line)
            # Watchdog expects a directory, so we watch the directory containing the log
            log_dir = "/".join(self.log_path.replace("\\", "/").split("/")[:-1])
            if not log_dir:
                log_dir = "."
                
            self.observer.schedule(event_handler, log_dir, recursive=False)
            self.observer.start()
            logger.info(f"Started watching log file: {self.log_path}")
        except FileNotFoundError:
            logger.error(f"Log file not found: {self.log_path}. Creating it.")
            open(self.log_path, 'w').close()
            self.start_watching()
        except Exception as e:
            logger.error(f"Failed to start LogCorrelator: {e}")

    def stop_watching(self):
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching log file.")

    def get_correlations(self):
        return self.crashes

if __name__ == "__main__":
    # Example usage:
    # correlator = LogCorrelator("server.log")
    # correlator.start_watching()
    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     correlator.stop_watching()
    pass
