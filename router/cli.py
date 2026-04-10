"""Ollama Router CLI - Main entry point."""

import asyncio
import os
import sys
from typing import List, Optional

import typer
from typer import Abort
from rich.console import Console
from rich.table import Table

from .config import RouterConfig
from .gateway import run_gateway
from .session_manager import SessionManager
from .rotation import ApiRotator
from .utils import (
    STYLE_ACTIVE,
    STYLE_COOLDOWN,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_HEADER,
    STYLE_SUCCESS,
    console,
    format_cooldown_time,
    get_status_label,
    get_status_style,
    mask_api_key,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
    truncate_middle,
)

app = typer.Typer(
    name="ollama-router",
    help="Rotate multiple Ollama API keys for uninterrupted Claude Code usage.",
    add_completion=True,
    pretty_exceptions_enable=True,
)

# Initialize config (lazy loading)
_config: Optional[RouterConfig] = None


def get_config() -> RouterConfig:
    """Get or create the config instance."""
    global _config
    if _config is None:
        _config = RouterConfig()
    return _config



def _fetch_available_models(api_key: str, api_base: str = "https://ollama.com/api") -> list:
    """Fetch available models from the Ollama API.

    Returns a list of model name strings, or empty list on failure.
    """
    import urllib.request
    import json

    import ssl

    api_key = api_key.strip()
    tags_url = f"{api_base.rstrip('/')}/tags"

    # Use unverified SSL context to avoid cert issues on macOS/Windows
    ctx = ssl._create_unverified_context()

    try:
        req = urllib.request.Request(tags_url)
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            models = data.get("models", [])
            names = []
            for m in models:
                name = m.get("name") or m.get("model") or ""
                if name:
                    names.append(name)
            return sorted(names)
    except urllib.error.HTTPError as e:
        print_error(f"API returned HTTP {e.code}. Check your API key.")
        return []
    except urllib.error.URLError as e:
        print_error(f"Could not connect to {tags_url}: {e.reason}")
        return []
    except Exception as e:
        print_error(f"Fetch failed: {type(e).__name__}: {e}")
        return []


def _validate_or_pick_model(api_key: str, api_base: str, model: str = None) -> str:
    """Validate a model name or let the user pick from available models.

    If model is provided, checks it exists. If not, shows a picker.
    Returns the validated model name.
    """
    from rich.prompt import Prompt, IntPrompt

    console.print()
    console.print("[dim]Fetching available models...[/dim]")

    models = _fetch_available_models(api_key, api_base)

    if not models:
        print_warning("Could not fetch model list. You can still enter a model name manually.")
        if model:
            return model
        return Prompt.ask("  Enter model name")

    # If model was provided, validate it
    if model:
        if model in models:
            print_success(f"Model '{model}' verified!")
            return model
        else:
            print_error(f"Model '{model}' not found on your account.")
            console.print()
            console.print("[bold]Available models:[/bold]")

    # Show numbered list for picking
    if not model:
        console.print()
        console.print(f"[bold]Found {len(models)} model(s) on your account:[/bold]")

    console.print()
    for i, m in enumerate(models):
        console.print(f"  [bold cyan]{i}[/bold cyan]  {m}")
    console.print()

    choice = Prompt.ask("Enter model number or name")

    # Check if it's a number
    try:
        idx = int(choice)
        if 0 <= idx < len(models):
            selected = models[idx]
            print_success(f"Selected: {selected}")
            return selected
    except ValueError:
        pass

    # It's a name — validate
    if choice in models:
        print_success(f"Selected: {choice}")
        return choice

    # Not found but user typed it — warn and use anyway
    print_warning(f"'{choice}' not in your model list. Using it anyway.")
    return choice


# ==================== Main Commands ====================


@app.command("add")
def add_api(
    name: Optional[str] = typer.Argument(
        None,
        help="Name for this API key (e.g., 'account-1'). If not provided, will prompt interactively.",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--key",
        "-k",
        help="The Ollama API key (sk-ollama-account-...). If not provided, will prompt interactively.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Model name to use (e.g., 'ollama-reasoner').",
    ),
    api_base: str = typer.Option(
        "https://ollama.com/api",
        "--base",
        "-b",
        help="API base URL.",
    ),
    litellm_model: Optional[str] = typer.Option(
        None,
        "--litellm-model",
        "-l",
        help="LiteLLM model string (e.g., 'ollama_chat/gemma-3-1b-reasoning').",
    ),
):
    """Add a new Ollama API key.

    Examples:
        ollama-router add
        ollama-router add my-account -k sk-ollama-...
        ollama-router add -k sk-ollama-... -m ollama-reasoner
    """
    config = get_config()

    print_header("Add New API Key")

    # Interactive prompts for missing values
    if not name:
        name = typer.prompt(
            "Enter a name for this API key",
            default=f"account-{len(config.list_apis()) + 1}",
        )

    if not api_key:
        api_key = typer.prompt(
            "Enter your Ollama API key",
            hide_input=True,
            confirmation_prompt=True,
        )

    # Validate model against available models on the account
    model = _validate_or_pick_model(api_key, api_base, model)

    if not litellm_model:
        litellm_model = ""

    try:
        entry = config.add_api(
            name=name,
            api_key=api_key,
            model_name=model,
            api_base=api_base,
            litellm_model=litellm_model,
        )

        console.print()
        print_success(f"Added API '{name}' successfully!")
        console.print()
        console.print(f"  Name:         {entry.name}")
        console.print(f"  API Key:      {mask_api_key(entry.api_key)}")
        console.print(f"  Model:        {entry.model_name}")
        console.print(f"  API Base:     {entry.api_base}")
        if entry.litellm_model:
            console.print(f"  LiteLLM:      {entry.litellm_model}")
        console.print()

    except ValueError as e:
        print_error(str(e))
        sys.exit(1)


@app.command("remove")
def remove_api(
    identifier: Optional[str] = typer.Argument(
        None,
        help="API name or index to remove (use 'ollama-router list' to see available APIs).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt.",
    ),
):
    """Remove an API key by name or index.

    Examples:
        ollama-router remove account-1
        ollama-router remove 0
    """
    config = get_config()

    # If no identifier provided, show interactive picker
    if identifier is None:
        apis = config.list_apis()
        if not apis:
            print_error("No APIs configured. Use 'ollama-router add' first.")
            sys.exit(1)

        print_header("Remove API")
        console.print()
        for i, api in enumerate(apis):
            console.print(f"  [bold cyan]{i}[/bold cyan]  {api.name}  ({mask_api_key(api.api_key)})  [dim]{api.model_name}[/dim]")
        console.print()

        from rich.prompt import Prompt
        identifier = Prompt.ask("Enter the name or index to remove")

    api = config.get_api(identifier)
    if not api:
        print_error(f"API '{identifier}' not found.")
        sys.exit(1)

    if not force:
        console.print()
        console.print(f"About to remove:")
        console.print(f"  Name:  {api.name}")
        console.print(f"  Model: {api.model_name}")
        console.print()

        confirm = typer.confirm("Are you sure you want to remove this API?")
        if not confirm:
            print_info("Cancelled.")
            return

    config.remove_api(api.name)
    print_success(f"Removed API '{api.name}' successfully!")


@app.command("edit")
def edit_api(
    identifier: Optional[str] = typer.Argument(
        None,
        help="API name or index to edit.",
    ),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="New name."),
    api_key: Optional[str] = typer.Option(None, "--key", "-k", help="New API key."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="New model name."),
    api_base: Optional[str] = typer.Option(None, "--base", "-b", help="New API base URL."),
):
    """Edit an existing API configuration.

    If no flags are provided, opens an interactive editor.

    Examples:
        ollama-router edit                          # Interactive picker + editor
        ollama-router edit my-key -m qwen3:72b      # Change model directly
        ollama-router edit 0 --key NEW_KEY          # Change API key
    """
    config = get_config()

    # If no identifier, show interactive picker
    if identifier is None:
        apis = config.list_apis()
        if not apis:
            print_error("No APIs configured. Use 'ollama-router add' first.")
            sys.exit(1)

        print_header("Edit API")
        console.print()
        for i, api in enumerate(apis):
            console.print(f"  [bold cyan]{i}[/bold cyan]  {api.name}  ({mask_api_key(api.api_key)})  [dim]{api.model_name}[/dim]")
        console.print()

        from rich.prompt import Prompt
        identifier = Prompt.ask("Enter the name or index to edit")

    api = config.get_api(identifier)
    if not api:
        print_error(f"API '{identifier}' not found.")
        sys.exit(1)

    # If no flags provided, run interactive editor
    if all(v is None for v in [name, api_key, model, api_base]):
        from rich.prompt import Prompt

        console.print()
        console.print(f"Editing [bold cyan]{api.name}[/bold cyan] — press Enter to keep current value")
        console.print()

        new_name = Prompt.ask("  Name", default=api.name)
        new_key = Prompt.ask("  API Key", default=api.api_key, password=True)
        new_model = _validate_or_pick_model(new_key if new_key != api.api_key else api.api_key, api.api_base, None)

        updates = {}
        if new_name != api.name:
            updates["name"] = new_name
        if new_key != api.api_key:
            updates["api_key"] = new_key
        if new_model != api.model_name:
            updates["model_name"] = new_model
    else:
        # Use provided flags
        updates = {}
        if name is not None:
            updates["name"] = name
        if api_key is not None:
            updates["api_key"] = api_key
        if model is not None:
            model = _validate_or_pick_model(api.api_key, api.api_base, model)
            updates["model_name"] = model
        if api_base is not None:
            updates["api_base"] = api_base

    if not updates:
        print_info("No changes made.")
        return

    result = config.update_api(api.name, **updates)
    if result:
        console.print()
        print_success(f"Updated API '{api.name}' successfully!")
        console.print()
        console.print(f"  Name:     {result.name}")
        console.print(f"  API Key:  {mask_api_key(result.api_key)}")
        console.print(f"  Model:    {result.model_name}")
        console.print(f"  API Base: {result.api_base}")
    else:
        print_error("Failed to update API.")


@app.command("list")
def list_apis():
    """List all configured API keys with their status."""
    config = get_config()
    rotator = ApiRotator(config)

    print_header("Configured APIs")

    apis = config.list_apis()

    if not apis:
        print_info("No APIs configured. Use 'ollama-router add' to add one.")
        return

    table = Table(
        title=f"{len(apis)} API(s) configured",
        show_lines=True,
    )

    table.add_column("Index", style=STYLE_DIM, justify="right")
    table.add_column("Name", style=STYLE_HEADER)
    table.add_column("API Key", style=STYLE_DIM)
    table.add_column("Model", style=STYLE_ACTIVE)
    table.add_column("Status", justify="center")
    table.add_column("Requests", justify="right")
    table.add_column("Cooldown", justify="center")

    current_index = config.get_current_index()

    for i, api in enumerate(apis):
        status_label = get_status_label(api.model_dump())
        cooldown_str = format_cooldown_time(
            api.cooldown_until if api.cooldown_until else None
        )

        # Add current indicator
        name_display = api.name
        if i == current_index:
            name_display = f"→ {name_display} ←"

        table.add_row(
            str(i),
            name_display,
            mask_api_key(api.api_key),
            api.model_name,
            status_label,
            str(api.total_requests),
            cooldown_str if cooldown_str != "N/A" else "-",
        )

    console.print(table)
    console.print()
    print_info(f"Total rotations: {config.get_total_rotations()}")


@app.command("status")
def show_status():
    """Show detailed router status and statistics."""
    config = get_config()
    rotator = ApiRotator(config)

    print_header("Router Status")

    stats = config.get_stats()
    current_api = rotator.get_current_api()

    console.print("Configuration:")
    console.print(f"  Config file:     {config.config_path}")
    console.print(f"  Total APIs:      {stats['total_apis']}")
    console.print(f"  Active APIs:     {stats['active_apis']}")
    console.print(f"  On cooldown:     {stats['on_cooldown']}")
    console.print()

    console.print("Rotation:")
    console.print(f"  Current index:   {stats['current_index']}")
    console.print(f"  Total rotations: {stats['total_rotations']}")
    if current_api:
        console.print(f"  Current API:     {current_api.name}")
    console.print()

    # Show API details
    apis = config.list_apis()
    if apis:
        console.print("API Details:")
        for i, api in enumerate(apis):
            status = get_status_label(api.model_dump())
            marker = "●" if i == config.get_current_index() else "○"
            console.print(
                f"  {marker} [{status.lower()}] {api.name}: {api.total_requests} requests, "
                f"{api.failed_count} failures"
            )


@app.command("use")
def use_api(
    identifier: str = typer.Argument(
        ...,
        help="API name or index to use.",
    ),
):
    """Set a specific API as the current one to use."""
    config = get_config()

    api = config.get_api(identifier)
    if not api:
        print_error(f"API '{identifier}' not found.")
        sys.exit(1)

    # Find index
    apis = config.list_apis()
    try:
        index = apis.index(api)
        config.set_current_index(index)
        print_success(f"Set '{api.name}' as current API.")
    except ValueError:
        print_error(f"Could not find API index.")
        sys.exit(1)


@app.command("reset-cooldowns")
def reset_cooldowns(
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation.",
    ),
):
    """Clear all API cooldowns."""
    config = get_config()

    if not confirm:
        count = sum(1 for api in config.list_apis() if config.is_on_cooldown(api))
        if count == 0:
            print_info("No cooldowns to clear.")
            return

        if not typer.confirm(
            f"Clear {count} cooldown(s)?",
            default=True,
        ):
            print_info("Cancelled.")
            return

    count = config.reset_all_cooldowns()
    print_success(f"Cleared {count} cooldown(s).")


@app.command("reset-stats")
def reset_stats(
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Reset all statistics and cooldowns.",
    ),
):
    """Reset all statistics and cooldowns."""
    config = get_config()

    if not confirm:
        typer.confirm("Reset all statistics and cooldowns?", default=True)

    config.reset_stats()
    print_success("Reset all statistics and cooldowns.")


@app.command("check")
def check_apis():
    """Check health of all configured APIs."""
    config = get_config()
    rotator = ApiRotator(config)

    print_header("API Health Check")

    apis = config.list_apis()
    if not apis:
        print_info("No APIs configured.")
        return

    async def run_checks():
        results = await rotator.check_all_apis()
        return results

    results = asyncio.run(run_checks())

    table = Table(title="Health Check Results")
    table.add_column("Name", style=STYLE_HEADER)
    table.add_column("Status", justify="center")
    table.add_column("Details")

    for name, (is_healthy, message) in results.items():
        status = "✓ Healthy" if is_healthy else "✗ Unhealthy"
        style = STYLE_ACTIVE if is_healthy else STYLE_ERROR
        table.add_row(name, f"[{style}]{status}[/{style}]", message)

    console.print(table)


# ==================== Auto-Launch Helper ====================


def _open_new_terminal_with_launch() -> bool:
    """Open a new terminal window and run 'ollama-router launch'.

    Cross-platform support:
      - macOS:   Terminal.app via osascript
      - Windows: cmd.exe via 'start'
      - Linux:   tries gnome-terminal, konsole, xfce4-terminal, xterm

    Returns True if a terminal was successfully opened.
    """
    import shutil
    import subprocess as sp

    # Get full path so it works even if PATH differs in new terminal
    router_bin = shutil.which("ollama-router")
    if not router_bin:
        router_bin = "ollama-router"

    launch_cmd = f"{router_bin} launch"
    platform = sys.platform

    try:
        if platform == "darwin":
            sp.Popen([
                "osascript", "-e",
                f'tell application "Terminal" to do script "sleep 3 && {launch_cmd}"'
            ])
            return True

        elif platform == "win32":
            sp.Popen(
                f'start "Claude Code" cmd /k "timeout /t 3 /nobreak > nul & {launch_cmd}"',
                shell=True
            )
            return True

        else:
            sleep_cmd = f"sleep 3 && {launch_cmd}"
            terminals = [
                ["gnome-terminal", "--", "bash", "-c", sleep_cmd],
                ["konsole", "-e", "bash", "-c", sleep_cmd],
                ["xfce4-terminal", "-e", f"bash -c '{sleep_cmd}'"],
                ["x-terminal-emulator", "-e", f"bash -c '{sleep_cmd}'"],
                ["xterm", "-e", f"bash -c '{sleep_cmd}'"],
            ]
            for term_cmd in terminals:
                if shutil.which(term_cmd[0]):
                    sp.Popen(term_cmd)
                    return True
            return False

    except Exception:
        return False


# ==================== Proxy Command ====================


@app.command("proxy")
def start_proxy(
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help="Ollama model to use (e.g., qwen3.5).",
    ),
    ollama_base: str = typer.Option(
        "http://localhost:11434", "--ollama-base",
        help="Ollama API base URL.",
    ),
    port: int = typer.Option(
        8082, "--port", "-p",
        help="Port to run Gateway proxy on.",
    ),
    auto_launch: bool = typer.Option(
        True, "--launch/--no-launch", "-l",
        help="Automatically open a new terminal and launch Claude Code (default: on).",
    ),
):
    """Start the API Gateway for Ollama.

    Examples:
        ollama-router proxy              # Start proxy only
        ollama-router proxy --launch     # Start proxy + auto-launch Claude Code
    """
    from rich.panel import Panel

    print_header("Ollama Router Gateway")

    if auto_launch:
        launched = _open_new_terminal_with_launch()
        if launched:
            console.print()
            console.print(Panel(
                f"[bold green]\u2713 Proxy starting on port {port}[/bold green]\n\n"
                f"[bold green]\u2713 Claude Code launching in a new terminal window[/bold green]\n\n"
                f"[dim]Keep this terminal running. It handles API routing & failover.\n"
                f"Claude Code will connect automatically in a few seconds.[/dim]",
                title="[bold cyan]Gateway[/bold cyan]",
                border_style="green",
                padding=(1, 3),
            ))
        else:
            console.print()
            console.print(Panel(
                f"[bold green]\u2713 Proxy starting on port {port}[/bold green]\n\n"
                f"[bold yellow]Could not open a new terminal automatically.[/bold yellow]\n\n"
                f"[bold]Please open a new terminal and run:[/bold]\n\n"
                f"  [bold white on #1a1a2e]  ollama-router launch  [/bold white on #1a1a2e]\n\n"
                f"[dim]Keep this terminal running. It handles API routing & failover.[/dim]",
                title="[bold cyan]Gateway[/bold cyan]",
                border_style="yellow",
                padding=(1, 3),
            ))
    else:
        console.print()
        console.print(Panel(
            f"[bold green]\u2713 Proxy starting on port {port}[/bold green]\n\n"
            f"[bold]Next Step:[/bold] Open a [bold cyan]new terminal[/bold cyan] and run:\n\n"
            f"  [bold white on #1a1a2e]  ollama-router launch  [/bold white on #1a1a2e]\n\n"
            f"[dim]This will start Claude Code connected through this proxy.\n"
            f"Keep this terminal running. It handles API routing & failover.[/dim]",
            title="[bold cyan]Gateway[/bold cyan]",
            border_style="green",
            padding=(1, 3),
        ))

    try:
        run_gateway(port=port)
    except KeyboardInterrupt:
        console.print()
        print_info("Stopping gateway...")


# ==================== Launch Command ====================


@app.command("launch")
def launch(
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        "-p",
        help="Initial prompt to send to Claude Code.",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i",
        help="Run in interactive mode.",
    ),
    args: Optional[List[str]] = typer.Argument(
        None,
        help="Additional arguments to pass to Claude Code.",
    ),
):
    """Launch Claude Code with API rotation.

    This command starts Claude Code using the current API configuration.
    If a rate limit is hit, it will automatically rotate to the next API.

    Examples:
        ollama-router launch
        ollama-router launch -p "Help me write a Python script"
        ollama-router launch -- --no-stream
    """
    config = get_config()

    if not config.list_apis():
        print_error("No APIs configured. Use 'ollama-router add' first.")
        sys.exit(1)

    # Auto-set gateway URL — the session manager always routes through the gateway
    proxy_url = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8082")
    os.environ["ANTHROPIC_BASE_URL"] = proxy_url

    # Check if gateway is reachable
    console.print()
    print_info(f"Connecting to gateway at {proxy_url}...")
    import urllib.request

    gateway_ok = False
    try:
        health_url = proxy_url.replace("/v1", "") if proxy_url.endswith("/v1") else proxy_url
        if not health_url.endswith("/"):
            health_url += "/"
        req = urllib.request.urlopen(f"{health_url}health", timeout=3)
        if req.getcode() == 200:
            print_success("Gateway is running!")
            gateway_ok = True
    except Exception:
        pass

    if not gateway_ok:
        print_error("Gateway is not running!")
        console.print()
        console.print("  Start it in another terminal first:")
        console.print("  [bold cyan]ollama-router proxy[/bold cyan]")
        console.print()
        if not typer.confirm("Continue anyway?", default=False):
            sys.exit(0)

    manager = SessionManager(config)

    print_header("Launching Claude Code")
    print_info("Press Ctrl+C to interrupt. APIs will rotate automatically on rate limits.")
    print_info(f"Using gateway: {proxy_url}")
    console.print()

    # Handle args properly
    extra_args = args if args else []

    try:
        if interactive:
            exit_code = asyncio.run(manager.launch_interactive(args=extra_args, prompt=prompt))
        else:
            # Non-interactive rotation is handled by the SessionManager similarly
            # For now, we use the interactive method as the primary entry point
            exit_code = asyncio.run(manager.launch_interactive(args=extra_args, prompt=prompt))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print_info("\nInterrupted.")
        sys.exit(130)


@app.command("claude")
def launch_claude_direct(
    args: List[str] = typer.Argument(None, help="Arguments to pass to Claude Code."),
):
    """Launch Claude Code directly (alias for 'launch').

    This is a convenient shortcut command.

    Examples:
        ollama-router claude
        ollama-router claude -p "Write a function"
        ollama-router claude -- --help
    """
    # Redirect to launch command
    config = get_config()

    if not config.list_apis():
        print_error("No APIs configured. Use 'ollama-router add' first.")
        sys.exit(1)

    manager = SessionManager(config)

    print_header("Launching Claude Code")
    console.print()

    try:
        exit_code = asyncio.run(manager.launch_interactive(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print_info("\nInterrupted.")
        sys.exit(130)


# ==================== Version & Help ====================


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
    ),
):
    """Ollama Router - Rotate multiple Ollama API keys.

    Quick Start:
        1. ollama-router add           # Add your first API key
        2. ollama-router list          # Verify it's configured
        3. ollama-router launch claude # Start using Claude Code

    When rate limited, the router automatically switches to the next API.
    """
    if version:
        from . import __version__

        console.print(f"ollama-router v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        # Interactive home menu
        from . import __version__
        from rich.panel import Panel
        from rich.prompt import Prompt

        config = get_config()
        api_count = len(config.list_apis())

        console.print()
        console.print(Panel(
            f"[bold]Ollama Router[/bold] v{__version__}\n"
            f"[dim]Route Claude Code through any Ollama model[/dim]\n\n"
            f"APIs configured: [bold cyan]{api_count}[/bold cyan]\n\n"
            f"[bold]How to use:[/bold] Press [bold white]1[/bold white] or run [white]ollama-router proxy[/white] to start the proxy.\n"
            f"  A new terminal will open automatically with Claude Code.\n"
            f"  If it fails, open a new terminal and run: [white]ollama-router launch[/white]",
            border_style="cyan",
            padding=(1, 3),
        ))

        console.print("  [bold cyan]1[/bold cyan]  📡  Start Proxy")
        console.print("  [bold cyan]2[/bold cyan]  🎯  Pick Starting API (optional)")
        console.print("  [bold cyan]3[/bold cyan]  ➕  Add API")
        console.print("  [bold cyan]4[/bold cyan]  ✏️   Edit API")
        console.print("  [bold cyan]5[/bold cyan]  🗑️   Remove API")
        console.print("  [bold cyan]6[/bold cyan]  📋  List APIs")
        console.print("  [bold cyan]7[/bold cyan]  🔍  Health Check")
        console.print("  [bold cyan]8[/bold cyan]  📊  Status")
        console.print("  [bold cyan]0[/bold cyan]  ❌  Exit")
        console.print()

        choice = Prompt.ask("Select an option", choices=["0","1","2","3","4","5","6","7","8"], default="0")

        if choice == "1":
            start_proxy(port=8082, model=None, auto_launch=True)
        elif choice == "2":
            # Interactive API selector
            apis = config.list_apis()
            if not apis:
                print_error("No APIs configured. Use option 3 to add one first.")
            else:
                console.print()
                for i, a in enumerate(apis):
                    current = " [bold green]← current[/bold green]" if i == config.get_current_index() else ""
                    console.print(f"  [bold cyan]{i}[/bold cyan]  {a.name}  [dim]({a.model_name})[/dim]{current}")
                console.print()
                pick = Prompt.ask("Select API to start with", choices=[str(i) for i in range(len(apis))])
                use_api(identifier=pick)
        elif choice == "3":
            add_api(name=None, api_key=None, model=None, api_base="https://ollama.com/api", litellm_model=None)
        elif choice == "4":
            edit_api(identifier=None, name=None, api_key=None, model=None, api_base=None)
        elif choice == "5":
            remove_api(identifier=None, force=False)
        elif choice == "6":
            list_apis()
        elif choice == "7":
            check_apis()
        elif choice == "8":
            show_status()
        elif choice == "0":
            console.print("[dim]Goodbye![/dim]")
            raise typer.Exit()


# ==================== Entry Point ====================


if __name__ == "__main__":
    app()
