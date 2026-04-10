"""
Self-Verification Tool for Ollama Router.
Launches the proxy and tests the connection to ensure the 'Model-Sling' and 'Gateway' logic works.
"""

import asyncio
import os
import subprocess
import httpx
import sys
from .gateway import run_gateway
from .config import RouterConfig

async def test_gateway_health():
    print("Testing Gateway Health...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get("http://localhost:8082/health")
            if resp.status_code == 200:
                print("✓ Gateway Health: OK")
                return True
        except Exception as e:
            print(f"✗ Gateway Health failed: {e}")
    return False

async def test_model_list():
    print("Testing Model List (Pre-flight check)...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get("http://localhost:8082/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = [m['id'] for m in data['data']]
                print(f"✓ Model List: OK ({len(models)} models returned)")
                if "claude-3-5-sonnet-20241022" in models:
                    print("✓ Expected model found in list.")
                    return True
                print(f"✗ Expected model not found. Found: {models}")
        except Exception as e:
            print(f"✗ Model List request failed: {e}")
    return False

async def main():
    # Start proxy in background
    print("Starting Gateway in background...")
    proxy_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "router.gateway:app", "--host", "0.0.0.0", "--port", "8082"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait for startup
    await asyncio.sleep(3)

    try:
        health = await test_gateway_health()
        models = await test_model_list()

        if health and models:
            print("\n✅ ALL SYSTEMS GO: Gateway is responding correctly and model validation should pass.")
        else:
            print("\n❌ VERIFICATION FAILED: Gateway is not behaving as expected.")

    finally:
        proxy_proc.terminate()
        print("Cleaned up proxy process.")

if __name__ == "__main__":
    asyncio.run(main())
