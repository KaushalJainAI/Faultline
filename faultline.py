#!/usr/bin/env python3
"""
Faultline interactive CLI.

Single entry point for running the Faultline pipeline / agent / hybrid mode
with rich terminal output, real-time agent reasoning, and human-in-the-loop
prompts for credentials and destructive-action approval.

Usage:
    python faultline.py                                                     # interactive prompts
    python faultline.py --target-dir . --mode pipeline                      # static pipeline only
    python faultline.py --target-dir /path --target-url http://localhost:8000 --mode agent
    python faultline.py --target-dir . --target-url http://localhost:8000 --mode hybrid

Flags:
    --no-hitl       disable HITL prompts (fully autonomous, auto-approve all)
    --no-semantic   skip FAISS semantic indexing in the pipeline phase
    --prompt        override the initial agent prompt
    --log-file      target server log file (default: server.log)
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: sys.path + Django setup so ORM tools work without runserver
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    import django
    django.setup()
except Exception as exc:
    print(f"[faultline] Warning: Django setup failed ({exc}). "
          f"DB-backed tools (record_finding) will be unavailable.")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Quiet down third-party loggers so the CLI output stays readable.
logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
for noisy in ("httpx", "urllib3", "asyncio", "watchdog"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

from rich.console import Console  # noqa: E402
from rich.prompt import Prompt    # noqa: E402

from core.cli_ui import CLIRenderer       # noqa: E402
from core.hitl import enable_hitl, hitl   # noqa: E402
from core.run_context import make_run_folder  # noqa: E402

console = Console()


# ---------------------------------------------------------------------------
# Argument parsing with interactive fallback
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="faultline",
        description="Faultline — interactive AI testing CLI",
    )
    parser.add_argument("--target-dir", help="Path to target project directory")
    parser.add_argument("--target-url", default="",
                        help="Base URL of target (required for agent / hybrid)")
    parser.add_argument("--mode", choices=["pipeline", "agent", "hybrid"],
                        default=None, help="Execution mode")
    parser.add_argument("--log-file", default="server.log",
                        help="Path to the target server log file")
    parser.add_argument("--prompt", default=None,
                        help="Override the initial agent prompt")
    parser.add_argument("--no-hitl", action="store_true",
                        help="Disable HITL prompts (auto-approve all actions)")
    parser.add_argument("--no-semantic", action="store_true",
                        help="Skip semantic indexing in the pipeline phase")
    parser.add_argument("--campaign-id", default="cli",
                        help="Campaign identifier used in the agent log filename")
    args = parser.parse_args()

    # Interactive fallback for missing required args.
    if not args.target_dir:
        args.target_dir = Prompt.ask(
            "[bold]Target project directory[/bold]",
            default=str(Path.cwd()),
        )

    if not args.mode:
        args.mode = Prompt.ask(
            "[bold]Execution mode[/bold]",
            choices=["pipeline", "agent", "hybrid"],
            default="hybrid",
        )

    if args.mode in {"agent", "hybrid"} and not args.target_url:
        args.target_url = Prompt.ask(
            "[bold]Target URL[/bold]",
            default="http://localhost:8000",
        )

    return args


# ---------------------------------------------------------------------------
# Provider validation — fail loudly before starting the agent
# ---------------------------------------------------------------------------

def validate_provider(target_dir: str, renderer: CLIRenderer) -> bool:
    try:
        from core.provider_config import get_config_status, get_cli_provider_name
        configured, message = get_config_status(target_dir)
        if not configured:
            console.print(f"[bold red]Provider not configured:[/bold red] {message}")
            console.print("[dim]Set FAULTLINE_PROVIDER and the matching API key, "
                          "or log in to claude/gemini/codex CLI.[/dim]")
            return False

        return True
    except Exception as exc:
        console.print(f"[yellow]Provider check failed: {exc}[/yellow]")
        return True  # don't block on a provider-check error; let the agent itself fail clearly


# ---------------------------------------------------------------------------
# Pipeline phase
# ---------------------------------------------------------------------------

def run_pipeline(args: argparse.Namespace, renderer: CLIRenderer, run_folder: Path) -> str:
    from core.pipeline import PipelineRunner
    console.print("\n[bold cyan]Pipeline phase[/bold cyan]")
    renderer.start_phase()
    runner = PipelineRunner(args.target_dir, run_folder=run_folder)
    result = runner.run(
        include_semantic=not args.no_semantic,
        renderer=renderer,
    )
    renderer.show_phase_timing("Pipeline phase")
    return result.get("report_path", "")


# ---------------------------------------------------------------------------
# Agent phase
# ---------------------------------------------------------------------------

async def run_agent(args: argparse.Namespace, renderer: CLIRenderer, run_folder: Path) -> None:
    from core.agent import AegisAgent
    console.print("\n[bold cyan]Agent phase[/bold cyan]")
    renderer.start_phase()

    initial_prompt = args.prompt or (
        "Run a full Faultline campaign against the target. "
        "First inspect the project structure with the file-listing and AST tools. "
        "Then write functional tests using the copy_test_boilerplate tool, validate them, "
        "and execute targeted chaos payloads. "
        "Save all test scripts to the Testcases Dir shown in your context. "
        "If you encounter an authentication challenge or need a credential, "
        "call the request_user_input tool with input_type='credential' to ask the operator. "
        "Record findings via record_finding so they appear in the final report."
    )

    agent = AegisAgent()
    await agent.run_campaign(
        target_dir=args.target_dir,
        target_url=args.target_url,
        log_file=args.log_file,
        run_folder=str(run_folder),
        initial_prompt=initial_prompt,
        campaign_id=args.campaign_id,
        renderer=renderer,
        hitl_manager=hitl if not args.no_hitl else None,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main_async() -> int:
    args = parse_args()

    if not args.no_hitl:
        enable_hitl()

    renderer = CLIRenderer(console=console)
    renderer.show_banner(
        target_dir=args.target_dir,
        target_url=args.target_url,
        mode=args.mode,
    )

    # Create the per-run output folder immediately so user sees it in the banner area.
    run_folder = make_run_folder(args.target_dir)
    renderer.show_run_folder(str(run_folder))

    if args.mode in {"agent", "hybrid"}:
        if not validate_provider(args.target_dir, renderer):
            return 2

    report_path = ""
    try:
        if args.mode in {"pipeline", "hybrid"}:
            report_path = run_pipeline(args, renderer, run_folder)

        if args.mode in {"agent", "hybrid"}:
            await run_agent(args, renderer, run_folder)
    except KeyboardInterrupt:
        console.print("\n[bold red]Aborted by user.[/bold red]")
        return 130
    except Exception as exc:
        console.print(f"\n[bold red]Campaign failed:[/bold red] {exc}")
        import traceback
        traceback.print_exc()
        return 1

    renderer.show_complete(report_path or str(run_folder))
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(main_async())
    except KeyboardInterrupt:
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
