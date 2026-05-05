#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import AegisAgent
from core.orchestration.pipeline import PipelineRunner


async def main():
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Faultline CLI debugging tool")
    parser.add_argument("--target-dir", required=True, help="Target project directory")
    parser.add_argument("--mode", choices=["pipeline", "agent", "hybrid"], default="hybrid")
    parser.add_argument("--target-url", default="", help="Base URL for agent/chaos testing")
    parser.add_argument("--log-file", default="server.log", help="Target server log file")
    parser.add_argument("--prompt", default="Run a Faultline debugging campaign.")
    parser.add_argument("--no-semantic", action="store_true", help="Skip FAISS documentation indexing")
    args = parser.parse_args()

    result = {}
    if args.mode in {"pipeline", "hybrid"}:
        result["pipeline"] = PipelineRunner(args.target_dir).run(include_semantic=not args.no_semantic)

    if args.mode in {"agent", "hybrid"}:
        if not args.target_url:
            raise SystemExit("--target-url is required for agent or hybrid mode.")
        agent = AegisAgent()
        result["agent"] = await agent.run_campaign(
            target_dir=args.target_dir,
            target_url=args.target_url,
            log_file=args.log_file,
            initial_prompt=args.prompt,
        )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
