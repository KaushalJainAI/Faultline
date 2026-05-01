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
    python faultline.py --resume reports/backend_20260501_143828            # resume from checkpoint

Flags:
    --no-hitl       disable HITL prompts (fully autonomous, auto-approve all)
    --no-semantic   skip FAISS semantic indexing in the pipeline phase
    --prompt        override the initial agent prompt
    --log-file      target server log file (default: server.log)
    --resume        resume a previous run from its checkpoint file or run folder
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

load_dotenv(override=True)

# Quiet down third-party loggers so the CLI output stays readable.
logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
for noisy in ("httpx", "urllib3", "asyncio", "watchdog"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

from rich.console import Console  # noqa: E402
from rich.prompt import Prompt    # noqa: E402

from core.cli_ui import CLIRenderer           # noqa: E402
from core.hitl import enable_hitl, hitl       # noqa: E402
from core.run_context import make_run_folder  # noqa: E402
from core.credential_store import init_store  # noqa: E402
from core.session_store import SessionStore   # noqa: E402

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
    parser.add_argument("--credentials", default=None,
                        help="Path to a credentials.toml file (overrides <target-dir>/.faultline/credentials.toml). "
                             "Use this to point at a file stored in Faultline/media/, e.g. "
                             "media/aiaas_credentials.toml")
    parser.add_argument("--resume", default=None,
                        help="Resume a previous run from its checkpoint file or run folder")
    # Budget / speed controls
    parser.add_argument("--reasoning-level", choices=["fast", "normal", "deep"], default=None,
                        help="Reasoning depth: fast (cheap), normal (default), deep (thorough)")
    parser.add_argument("--max-llm-calls", type=int, default=None,
                        help="Max number of LLM calls for the whole run (default: 20)")
    parser.add_argument("--max-tool-calls", type=int, default=None,
                        help="Max total tool calls across the run (default: 60)")
    parser.add_argument("--max-input-tokens", type=int, default=None,
                        help="Max context window tokens per LLM call (default: 200000)")
    parser.add_argument("--max-output-tokens", type=int, default=None,
                        help="Max output tokens per LLM call (overrides reasoning-level default)")
    args = parser.parse_args()

    # If resuming, skip interactive prompts — all state comes from the checkpoint
    if args.resume:
        return args

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

async def run_agent(
    args: argparse.Namespace,
    renderer: CLIRenderer,
    run_folder: Path,
    input_handler=None,
    resumed_messages=None,
    mode: str = "hybrid",
    session_store: SessionStore = None,
) -> None:
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

    # Load project memory if available
    if session_store:
        memory = session_store.read_memory()
        if memory:
            initial_prompt += (
                "\n\n--- PROJECT MEMORY (from previous sessions) ---\n"
                + memory
                + "\n--- END PROJECT MEMORY ---"
            )

    from core.agent import AegisAgent, BudgetConfig

    # Apply budget flags → env vars so BudgetConfig picks them up automatically.
    # We also set them explicitly in BudgetConfig so they always win over env defaults.
    if args.reasoning_level:
        os.environ["FAULTLINE_REASONING_LEVEL"] = args.reasoning_level
    if args.max_llm_calls is not None:
        os.environ["FAULTLINE_MAX_LLM_CALLS"] = str(args.max_llm_calls)
    if args.max_tool_calls is not None:
        os.environ["FAULTLINE_MAX_TOOL_CALLS"] = str(args.max_tool_calls)
    if args.max_input_tokens is not None:
        os.environ["FAULTLINE_MAX_TOKENS"] = str(args.max_input_tokens)
    if args.max_output_tokens is not None:
        os.environ["FAULTLINE_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)

    budget = BudgetConfig()
    console.print(
        f"[dim]Budget: reasoning={budget.reasoning_level}  "
        f"llm≤{budget.max_llm_calls}  tool≤{budget.max_tool_calls}  "
        f"in≤{budget.max_input_tokens:,}tok  out≤{budget.max_output_tokens}tok/call[/dim]"
    )

    agent = AegisAgent(budget=budget)
    await agent.run_campaign(
        target_dir=args.target_dir,
        target_url=args.target_url,
        log_file=args.log_file,
        run_folder=str(run_folder),
        initial_prompt=initial_prompt,
        campaign_id=getattr(args, 'campaign_id', 'cli'),
        renderer=renderer,
        hitl_manager=hitl if not args.no_hitl else None,
        input_handler=input_handler,
        resumed_messages=resumed_messages,
        mode=mode,
        session_store=session_store,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main_async() -> int:
    args = parse_args()

    if not args.no_hitl:
        enable_hitl()

    renderer = CLIRenderer(console=console)

    # ------------------------------------------------------------------
    # Resume flow — load checkpoint and skip straight to agent phase
    # ------------------------------------------------------------------
    if args.resume:
        from core.checkpoint import load_checkpoint
        from core.input_handler import InputHandler

        resume_path = args.resume
        # Accept both a run folder and a direct checkpoint.json path
        if resume_path.endswith(".json"):
            import json as _json
            data = _json.loads(Path(resume_path).read_text(encoding="utf-8"))
            from core.checkpoint import deserialize_messages
            data["messages"] = deserialize_messages(data.get("messages", []))
        else:
            data = load_checkpoint(resume_path)

        if not data:
            console.print(f"[bold red]No checkpoint found in:[/bold red] {resume_path}")
            return 1

        # Restore state from checkpoint
        args.target_dir = data.get("target_dir", ".")
        args.target_url = data.get("target_url", "")
        args.log_file = data.get("log_file", "server.log")
        args.mode = data.get("mode", "hybrid")
        if not hasattr(args, 'campaign_id'):
            args.campaign_id = "resumed"

        run_folder = Path(data.get("run_folder", resume_path))

        # Restore model override if saved
        if data.get("active_model"):
            from core.model_registry import set_active_model
            set_active_model(data["active_model"], data.get("active_provider", "openrouter"))

        # Restore session headers
        if data.get("session_headers"):
            from core.context import session_headers_var
            session_headers_var.set(data["session_headers"])

        renderer.show_banner(
            target_dir=args.target_dir,
            target_url=args.target_url,
            mode=args.mode,
        )
        renderer.show_resumed(turn=data.get("turn", 0), run_folder=str(run_folder))
        renderer.show_esc_hint()

        input_handler = InputHandler(console=console)

        # Restore or create session store
        session_store = SessionStore(target_dir=args.target_dir)
        session_store.create_session(
            mode=args.mode,
            target_url=args.target_url,
            log_file=args.log_file,
            run_folder=str(run_folder),
        )

        # Inject live_report.md into the resume prompt so agent doesn't repeat work
        live_report_path = run_folder / "live_report.md"
        if live_report_path.exists():
            live_report_content = live_report_path.read_text(encoding="utf-8")
            if live_report_content.strip():
                args.prompt = (
                    "You are resuming a previous campaign that was interrupted. "
                    "The following work has already been completed — do NOT repeat these steps. "
                    "Continue from where you left off, focusing only on what is NOT yet done.\n\n"
                    "--- PREVIOUS SESSION PROGRESS ---\n"
                    f"{live_report_content[:8000]}\n"
                    "--- END PREVIOUS SESSION ---\n\n"
                    "Resume the campaign now."
                )

        try:
            await run_agent(
                args, renderer, run_folder,
                input_handler=input_handler,
                resumed_messages=data.get("messages"),
                mode=args.mode,
                session_store=session_store,
            )
            session_store.finalize_session(status="completed", turn=data.get("turn", 0))
        except KeyboardInterrupt:
            from core.checkpoint import save_checkpoint
            save_checkpoint(
                run_folder=str(run_folder),
                messages=data.get("messages", []),
                turn=data.get("turn", 0),
                target_dir=args.target_dir,
                target_url=args.target_url,
                log_file=args.log_file,
                mode=args.mode,
            )
            session_store.finalize_session(status="paused", turn=data.get("turn", 0))
            console.print(
                f"\n[bold yellow]Interrupted.[/bold yellow] "
                f"Checkpoint saved. Resume with: python faultline.py --resume {run_folder}"
            )
            return 130

        renderer.show_complete(str(run_folder))
        return 0

    # ------------------------------------------------------------------
    # Normal flow
    # ------------------------------------------------------------------

    # Load target credentials and run full resolution chain at startup.
    # This pre-populates session_headers_var so the agent never needs to
    # trigger HITL for the default role — get_credential() will see these
    # headers and return immediately (source="session").
    cred_store = init_store(args.target_dir, credentials_path=args.credentials)
    if cred_store.loaded:
        console.print(f"[dim]{cred_store.summary()}[/dim]")
        from core.tools import resolve_credential_at_startup
        from core.context import session_headers_var
        auth_header = resolve_credential_at_startup(cred_store)
        if auth_header:
            session_headers_var.set(auth_header)
            console.print("  [dim green]Auth resolved at startup — agent will use pre-populated headers.[/dim green]")
        else:
            console.print(
                "  [dim yellow]Warning: could not resolve auth at startup "
                "(token/refresh/login all failed). "
                "The agent will skip auth tests or call request_user_input.[/dim yellow]"
            )
        if not args.target_url and cred_store.target_url():
            args.target_url = cred_store.target_url()
            console.print(f"[dim]  target url from credentials: {args.target_url}[/dim]")

    renderer.show_banner(
        target_dir=args.target_dir,
        target_url=args.target_url,
        mode=args.mode,
    )

    run_folder = make_run_folder(args.target_dir)
    renderer.show_run_folder(str(run_folder))
    renderer.show_esc_hint()

    if args.mode in {"agent", "hybrid"}:
        if not validate_provider(args.target_dir, renderer):
            return 2

    # Create input handler for Esc key detection
    from core.input_handler import InputHandler
    input_handler = InputHandler(console=console)

    # Create session store for persistent logging
    session_store = SessionStore(target_dir=args.target_dir)
    session_store.create_session(
        mode=args.mode,
        target_url=args.target_url,
        log_file=args.log_file,
        run_folder=str(run_folder),
    )
    console.print(f"  [dim]Session:[/dim] {session_store.session_id}")

    report_path = ""
    try:
        if args.mode in {"pipeline", "hybrid"}:
            report_path = run_pipeline(args, renderer, run_folder)

        if args.mode in {"agent", "hybrid"}:
            await run_agent(
                args, renderer, run_folder,
                input_handler=input_handler,
                mode=args.mode,
                session_store=session_store,
            )
        session_store.finalize_session(status="completed")
    except KeyboardInterrupt:
        from core.checkpoint import save_checkpoint
        save_checkpoint(
            run_folder=str(run_folder),
            messages=[],
            turn=0,
            target_dir=args.target_dir,
            target_url=args.target_url,
            log_file=args.log_file,
            mode=args.mode,
        )
        session_store.finalize_session(status="paused")
        console.print(
            f"\n[bold yellow]Interrupted.[/bold yellow] "
            f"Checkpoint saved. Resume with: python faultline.py --resume {run_folder}"
        )
        return 130
    except Exception as exc:
        session_store.finalize_session(status="error", summary=str(exc))
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
