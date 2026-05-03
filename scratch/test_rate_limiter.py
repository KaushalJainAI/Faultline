import asyncio
import time
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import LLMRateLimiter

async def test_rate_limiter():
    rpm = 10
    limiter = LLMRateLimiter(rpm)
    start_time = time.monotonic()
    
    print(f"Starting test with {rpm} RPM limit...")
    
    for i in range(1, 21):
        await limiter.wait()
        elapsed = time.monotonic() - start_time
        print(f"Call {i:2d} at {elapsed:5.2f}s")
        
    total_elapsed = time.monotonic() - start_time
    print(f"\nTest complete. Total time: {total_elapsed:.2f}s")
    
    # With 10 RPM, calls 1-10 should be instant.
    # Call 11 should wait until 60 seconds have passed since Call 1.
    # However, for a quick test, we can use a smaller window or just verify it blocks.
    # Actually, the implementation uses a fixed 60s window.
    # To test quickly, I'll monkeypatch the window size.

async def test_fast_window():
    rpm = 5
    limiter = LLMRateLimiter(rpm)
    limiter.window_size = 5.0  # 5 seconds instead of 60
    start_time = time.monotonic()
    
    print(f"\nStarting fast window test (RPM={rpm}, Window={limiter.window_size}s)...")
    
    for i in range(1, 11):
        await limiter.wait()
        elapsed = time.monotonic() - start_time
        print(f"Call {i:2d} at {elapsed:5.2f}s")
        
    total_elapsed = time.monotonic() - start_time
    print(f"\nTest complete. Total time: {total_elapsed:.2f}s")
    # Call 1-5: instant
    # Call 6: should wait until ~5s
    # Call 10: should wait until ~10s

if __name__ == "__main__":
    asyncio.run(test_fast_window())
