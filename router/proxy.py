"""Simple FastAPI proxy for Ollama Router - no LiteLLM dependency."""

import asyncio
import os
import signal
import subprocess
import sys
from typing import Optional

from .utils import console, print_error, print_info, print_success, print_warning


class SimpleProxy:
    """Manages the simple FastAPI proxy for Ollama-to-Anthropic translation."""

    DEFAULT_PORT = 8082
    DEFAULT_OLLAMA_BASE = "http://localhost:11434"

    def __init__(
        self,
        ollama_base: str = DEFAULT_OLLAMA_BASE,
        port: int = DEFAULT_PORT,
    ):
        """Initialize the proxy manager.

        Args:
            ollama_base: Ollama API base URL
            port: Port to run the proxy on
        """
        self.ollama_base = ollama_base
        self.port = port
        self._process: Optional[subprocess.Popen] = None
        self._proxy_url = f"http://localhost:{port}/v1"

    @property
    def proxy_url(self) -> str:
        """Get the proxy URL for ANTHROPIC_BASE_URL."""
        return self._proxy_url

    def is_running(self) -> bool:
        """Check if proxy is currently running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    async def start(self) -> bool:
        """Start the simple FastAPI proxy.

        Returns:
            True if proxy started successfully
        """
        if self.is_running():
            print_info("Proxy is already running.")
            return True

        console.print()
        print_info(f"Starting Ollama Router proxy on port {self.port}...")
        console.print(f" Ollama base: {self.ollama_base}")
        console.print()

        # Start the proxy using uvicorn
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "router.simple_proxy:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(self.port),
        ]

        env = os.environ.copy()
        env["OLLAMA_BASE"] = self.ollama_base

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )

            # Wait a moment for the proxy to start
            await asyncio.sleep(2)

            if self._process.poll() is not None:
                # Process failed to start
                output = self._process.stdout.read() if self._process.stdout else ""
                print_error(f"Proxy failed to start: {output}")
                self._process = None
                return False

            print_success(f"Ollama Router proxy started on {self._proxy_url}")
            print_info("Set ANTHROPIC_BASE_URL before launching Claude:")
            console.print(f" export ANTHROPIC_BASE_URL={self._proxy_url}")
            console.print()

            return True

        except Exception as e:
            print_error(f"Failed to start proxy: {e}")
            self._process = None
            return False

    async def stop(self) -> None:
        """Stop the proxy process."""
        if self._process and self.is_running():
            print_info("Stopping Ollama Router proxy...")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            print_success("Proxy stopped.")

    def get_env_vars(self) -> dict:
        """Get environment variables needed for Claude Code."""
        return {
            "ANTHROPIC_BASE_URL": self._proxy_url,
            "OLLAMA_PROXY_URL": self._proxy_url,
        }


async def start_proxy_interactive(ollama_base: Optional[str] = None, port: Optional[int] = None):
    """Start proxy in interactive mode with user prompts."""
    console.print()
    console.print("[bold cyan]Ollama Router Proxy[/bold cyan]")
    console.print()

    # Get Ollama base from user or use default
    if ollama_base is None:
        ollama_base = console.input(
            f"[bold]Ollama API base[/bold] (default: {SimpleProxy.DEFAULT_OLLAMA_BASE}): "
            f"[default]{SimpleProxy.DEFAULT_OLLAMA_BASE}[/default]\n"
        )
        if not ollama_base.strip():
            ollama_base = SimpleProxy.DEFAULT_OLLAMA_BASE

    # Get port from user or use default
    if port is None:
        port_str = console.input(
            f"[bold]Proxy port[/bold] (default: {SimpleProxy.DEFAULT_PORT}): "
            f"[default]{SimpleProxy.DEFAULT_PORT}[/default]\n"
        )
        try:
            port = int(port_str.strip()) if port_str.strip() else SimpleProxy.DEFAULT_PORT
        except ValueError:
            port = SimpleProxy.DEFAULT_PORT

    proxy = SimpleProxy(ollama_base=ollama_base, port=port)
    success = await proxy.start()

    if success:
        console.print()
        print_info("To launch Claude Code with this proxy:")
        console.print(f" export ANTHROPIC_BASE_URL={proxy.proxy_url}")
        console.print(" ollama-router launch claude")
        console.print()
        print_info("Press Ctrl+C to stop the proxy when done.")

        # Keep the proxy running and pass through signals
        try:
            while proxy.is_running():
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            await proxy.stop()
