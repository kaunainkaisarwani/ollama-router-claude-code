"""
Session Orchestrator for Ollama Router.
Manages isolated Claude Code CLI processes with transparent recovery and rotation.
"""

import asyncio
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from .config import ApiEntry, RouterConfig
from .rotation import ApiRotator, AllApisExhaustedError
from .utils import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
    STYLE_ACTIVE,
)

@dataclass
class CLISession:
    """Represents a single managed Claude Code CLI instance."""
    process: asyncio.subprocess.Process
    api: ApiEntry
    env: Dict[str, str]
    cmd: List[str]
    start_time: float

class SessionManager:
    """
    Orchestrates the lifecycle of Claude Code CLI processes.

    Implements 'Clean-Room' environment injection and automatic session
    recovery when the Gateway signals an API rotation.
    """

    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or RouterConfig()
        self.rotator = ApiRotator(self.config)
        self._current_session: Optional[CLISession] = None
        self._api_rotation_count = 0

    def _build_clean_room_env(self, api: ApiEntry) -> Dict[str, str]:
        """
        Builds a strictly isolated environment to prevent shell leakage
        and force the CLI to use the Gateway.
        """
        env = os.environ.copy()

        # 1. Clear all existing Anthropic/Claude auth to prevent conflicts
        auth_vars = ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_AUTH_TOKEN", "OAUTH_TOKEN", "ANTHROPIC_API_KEY"]
        for var in auth_vars:
            env.pop(var, None)

        # 2. Force Gateway Routing
        env["ANTHROPIC_BASE_URL"] = "http://localhost:8082"

        # 3. Provide a dummy key to satisfy the CLI's initial check
        # The Gateway will replace this with the real rotated key
        env["ANTHROPIC_API_KEY"] = "sk-ant-dummy-key-for-gateway"

        # 4. Keep the user's real TERM so Claude Code renders its TUI properly
        # (Setting TERM=dumb would disable colors, spinners, and the interactive UI)

        # 5. Router metadata
        env["OLLAMA_ROUTER_ACTIVE"] = "1"
        env["OLLAMA_ROUTER_API_NAME"] = api.name

        return env

    def _build_command(self, prompt: Optional[str] = None, args: Optional[List[str]] = None) -> List[str]:
        """Builds the Claude Code command with a trusted model for validation."""
        cmd = ["claude"]

        if args:
            cmd.extend(args)

        if prompt:
            if "-p" not in cmd and "--prompt" not in cmd:
                cmd.extend(["-p", prompt])

        # Always use a known valid Anthropic model ID to bypass client-side validation.
        # The Gateway will map this to the actual Ollama model.
        if "--model" not in cmd:
            cmd.extend(["--model", "claude-3-5-sonnet-20241022"])

        return cmd

    async def launch_interactive(
        self,
        args: Optional[List[str]] = None,
        prompt: Optional[str] = None
    ) -> int:
        """
        Launches Claude Code in interactive mode with automatic
        session recovery on rotation.
        """
        self._api_rotation_count = 0

        while self._api_rotation_count < 10:
            try:
                # 1. Get the next available API
                api = await self.rotator.get_next_api()

                console.print()
                print_info(f"╭─ Session Orchestrator: Using API {api.name}")
                print_info(f"│  Target Model: {api.model_name}")
                print_info(f"╰─ Gateway: http://localhost:8082/v1")
                console.print()

                # 2. Prepare Clean-Room environment and command
                env = self._build_clean_room_env(api)
                cmd = self._build_command(prompt, args)

                # 3. Launch process with direct terminal passthrough
                # Using asyncio subprocess to avoid blocking the event loop
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                )

                # Track the current session
                self._current_session = CLISession(
                    process=process,
                    api=api,
                    env=env,
                    cmd=cmd,
                    start_time=time.time(),
                )

                # 4. Wait for process to complete (non-blocking)
                returncode = await process.wait()

                # Clear session reference
                self._current_session = None

                # 5. Check for failure that might require rotation
                if returncode != 0:
                    print_warning(f"Claude Code exited with code {returncode}")

                    # The Gateway might have already triggered a rotation in the background
                    # If a rotation happened, we restart the session transparently.
                    if self._api_rotation_count < 10:
                        console.print()
                        response = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: input("Rotate API and restart session? [Y/n]: ").strip().lower(),
                        )

                        if response in ("", "y", "yes"):
                            await self.rotator.trigger_rotation("Process exit with error")
                            self._api_rotation_count += 1
                            continue

                    return returncode
                else:
                    await self.rotator.mark_success(api.api_key)
                    return 0

            except AllApisExhaustedError as e:
                print_error(str(e))
                return 1
            except KeyboardInterrupt:
                print_info("\nSession interrupted by user.")
                return 130
            except Exception as e:
                print_error(f"Orchestrator Error: {e}")
                return 1

        print_error("Exhausted all API rotations.")
        return 1

    def stop_current(self):
        """Terminate the currently running session."""
        if self._current_session and self._current_session.process.returncode is None:
            self._current_session.process.terminate()
            self._current_session = None
