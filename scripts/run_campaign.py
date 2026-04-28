#!/usr/bin/env python3
import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add the parent directory to sys.path so project modules can be imported.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import AegisAgent

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


async def main():
    parser = argparse.ArgumentParser(description="Run a Faultline campaign with Aegis-Breaker")
    parser.add_argument("--target-dir", type=str, required=True, help="Absolute path to the target project directory")
    parser.add_argument("--target-url", type=str, required=True, help="Base URL of the target application")
    parser.add_argument("--log-file", type=str, required=True, help="Path to the target server log file")
    parser.add_argument("--prompt", type=str, default="Begin the chaos campaign against the target.", help="Initial prompt for the agent")

    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY is not set. Add it to your .env file or environment.")
        sys.exit(1)

    print("\nStarting Aegis-Breaker campaign...")
    print(f"Target directory: {args.target_dir}")
    print(f"Target URL: {args.target_url}")
    print(f"Monitoring log: {args.log_file}")
    print("-" * 50)

    agent = AegisAgent()

    try:
        result = await agent.run_campaign(
            target_dir=args.target_dir,
            target_url=args.target_url,
            log_file=args.log_file,
            initial_prompt=args.prompt,
        )
        print("\nCampaign execution finished.")
        print(f"Result: {result}")
    except KeyboardInterrupt:
        print("\nCampaign aborted by user.")
    except Exception as e:
        print(f"\nError during campaign: {e}")


if __name__ == "__main__":
    asyncio.run(main())
