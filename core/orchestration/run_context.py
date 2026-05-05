from datetime import datetime
from pathlib import Path


def make_run_folder(target_dir: str, suffix: str = "", reports_base: str = "reports") -> Path:
    """
    Creates and returns a timestamped, project-named folder for a single campaign run.

    Layout:  reports/{project_name}_{YYYYMMDD_HHMMSS}[_{suffix}]/
                testcases/   â† test scripts land here

    Both faultline.py (CLI) and campaigns/services.py (REST) use this so every
    runâ€”interactive or headlessâ€”gets its own isolated output directory.
    """
    project_name = Path(target_dir).name.lower().replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{project_name}_{timestamp}"
    if suffix:
        name = f"{name}_{suffix}"
    folder = Path(reports_base) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "testcases").mkdir(exist_ok=True)
    return folder

