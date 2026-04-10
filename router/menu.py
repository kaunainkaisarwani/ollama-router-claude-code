"""Interactive TUI menu for Ollama Router."""

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm

from .config import RouterConfig
from .session_manager import SessionManager
from .rotation import ApiRotator
from .utils import (
    STYLE_ACTIVE,
    STYLE_COOLDOWN,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_HEADER,
    STYLE_SUCCESS,
    STYLE_WARNING,
    console,
    format_cooldown_time,
    get_status_label,
    mask_api_key,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


def render_home_screen(config: RouterConfig) -> None:
    """Render the home screen dashboard."""
    from rich.layout import Layout
    from rich.table import Table

    stats = config.get_stats()
    apis = config.list_apis()

    # Header
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]🔄 Ollama Router[/bold cyan]\n"
            "[dim]Rotate multiple API keys for uninterrupted Claude Code[/dim]",
            border_style="cyan",
        ),
        justify="center",
    )
    console.print()

    # Stats row
    stats_table = Table(show_header=False, box=None, padding=(0, 2))
    stats_table.add_column("Label", style=STYLE_DIM)
    stats_table.add_column("Value", style=STYLE_SUCCESS)

    stats_table.add_row("Total APIs", str(stats["total_apis"]))
    stats_table.add_row("Active", str(stats["active_apis"]))
    stats_table.add_row("On Cooldown", str(stats["on_cooldown"]))
    stats_table.add_row("Total Rotations", str(stats["total_rotations"]))

    console.print(
        Panel(stats_table, title="📊 Statistics", border_style="green"),
    )
    console.print()

    # APIs table
    if apis:
        api_table = Table(title="Configured APIs", show_lines=True)
        api_table.add_column("#", style=STYLE_DIM, justify="right")
        api_table.add_column("Name", style=STYLE_HEADER)
        api_table.add_column("API Key", style=STYLE_DIM)
        api_table.add_column("Model", style=STYLE_ACTIVE)
        api_table.add_column("Status", justify="center")
        api_table.add_column("Requests", justify="right")

        current_index = config.get_current_index()

        for i, api in enumerate(apis):
            status_label = get_status_label(api.model_dump())
            name_display = f"👉 {api.name}" if i == current_index else api.name

            api_table.add_row(
                str(i),
                name_display,
                mask_api_key(api.api_key),
                api.model_name,
                status_label,
                str(api.total_requests),
            )

        console.print(api_table)
    else:
        console.print(
            Panel(
                "[yellow]⚠ No APIs configured yet![/yellow]\n\n"
                "Select option [bold]1[/bold] to add your first API key.",
                title="Getting Started",
                border_style="yellow",
            )
        )

    console.print()


def render_menu_options() -> None:
    """Render menu options."""
    console.print("[bold]Main Menu:[/bold]")
    console.print()
    console.print("  [bold cyan]1[/bold cyan]  [green]➕ Add API Key[/green]")
    console.print("  [bold cyan]2[/bold cyan]  [blue]📋 List APIs[/blue]")
    console.print("  [bold cyan]3[/bold cyan]  [magenta]⚙️  Manage APIs[/magenta]")
    console.print("  [bold cyan]4[/bold cyan]  [yellow]🔄 Reset Cooldowns[/yellow]")
    console.print("  [bold cyan]5[/bold cyan]  [green]🚀 Launch Claude Code[/green]")
    console.print("  [bold cyan]6[/bold cyan]  [blue]❤️  Health Check[/blue]")
    console.print("  [bold cyan]0[/bold cyan]  [red]🚪 Exit[/red]")
    console.print()


def handle_add_api(config: RouterConfig) -> None:
    """Handle add API flow."""
    print_header("Add New API Key")

    name = typer.prompt(
        "Enter a name for this API key",
        default=f"account-{len(config.list_apis()) + 1}",
    )

    api_key = typer.prompt(
        "Enter your Ollama API key",
        hide_input=True,
        confirmation_prompt=True,
    )

    model = typer.prompt(
        "Enter the model name",
        default="ollama-reasoner",
    )

    litellm_model = ""
    try:
        litellm_model = typer.prompt(
            "Enter LiteLLM model string (optional, press Enter to skip)",
            default="",
            show_default=False,
        )
    except typer.Abort:
        pass

    try:
        entry = config.add_api(
            name=name,
            api_key=api_key,
            model_name=model,
            api_base="https://ollama.com/api",
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

    typer.prompt("Press Enter to continue")


def handle_list_apis(config: RouterConfig) -> None:
    """Handle list APIs display."""
    from rich.table import Table

    print_header("Configured APIs")

    apis = config.list_apis()

    if not apis:
        print_info("No APIs configured.")
        typer.prompt("Press Enter to continue")
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

        name_display = f"→ {api.name} ←" if i == current_index else api.name

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

    typer.prompt("Press Enter to continue")


def handle_manage_apis(config: RouterConfig) -> None:
    """Handle API management submenu."""
    while True:
        console.clear()
        print_header("Manage APIs")

        apis = config.list_apis()

        if not apis:
            print_info("No APIs to manage.")
            typer.prompt("Press Enter to continue")
            return

        # Show APIs
        for i, api in enumerate(apis):
            status = get_status_label(api.model_dump())
            current_marker = " 👉" if i == config.get_current_index() else "  "
            console.print(
                f"  [{status.lower()}]{i}. {api.name}{current_marker}[/]"
            )
        console.print()

        # Submenu
        console.print("[bold]Actions:[/bold]")
        console.print("  [bold]S[/bold]  Set as current API")
        console.print("  [bold]R[/bold]  Remove API")
        console.print("  [bold]C[/bold]  Clear cooldown for API")
        console.print("  [bold]B[/bold]  Back to main menu")
        console.print()

        choice = Prompt.ask(
            "Select action",
            choices=["S", "R", "C", "B", "s", "r", "c", "b"],
            default="B",
        ).upper()

        if choice == "B":
            return
        elif choice == "S":
            idx = IntPrompt.ask("Enter API index to set as current", default=0)
            if 0 <= idx < len(apis):
                config.set_current_index(idx)
                print_success(f"Set '{apis[idx].name}' as current API.")
            else:
                print_error("Invalid index.")
            typer.prompt("Press Enter to continue")
        elif choice == "R":
            idx = IntPrompt.ask("Enter API index to remove", default=0)
            if 0 <= idx < len(apis):
                if Confirm.ask(f"Remove '{apis[idx].name}'?"):
                    config.remove_api(str(idx))
                    print_success(f"Removed '{apis[idx].name}'.")
            else:
                print_error("Invalid index.")
            typer.prompt("Press Enter to continue")
        elif choice == "C":
            idx = IntPrompt.ask("Enter API index to clear cooldown", default=0)
            if 0 <= idx < len(apis):
                if config.clear_cooldown(str(idx)):
                    print_success(f"Cleared cooldown for '{apis[idx].name}'.")
                else:
                    print_info("No cooldown to clear.")
            else:
                print_error("Invalid index.")
            typer.prompt("Press Enter to continue")


def handle_launch_claude(config: RouterConfig) -> None:
    """Handle Claude Code launch."""
    apis = config.list_apis()

    if not apis:
        print_error("No APIs configured. Add an API first.")
        typer.prompt("Press Enter to continue")
        return

    console.print()
    console.print(
        Panel(
            "[bold]Launching Claude Code[/bold]\n\n"
            "APIs will rotate automatically when rate limits are hit.\n\n"
            "[dim]Press Ctrl+C to interrupt.[/dim]",
            border_style="green",
        )
    )
    console.print()

    if not Confirm.ask("Ready to launch?"):
        return

    launcher = SessionManager(config)

    try:
        import asyncio

        asyncio.run(launcher.launch_interactive([]))
    except KeyboardInterrupt:
        print_info("\nInterrupted.")


def handle_health_check(config: RouterConfig) -> None:
    """Handle health check."""
    print_header("API Health Check")

    apis = config.list_apis()

    if not apis:
        print_info("No APIs configured.")
        typer.prompt("Press Enter to continue")
        return

    rotator = ApiRotator(config)

    async def run_checks():
        results = await rotator.check_all_apis()
        return results

    import asyncio

    console.print("[dim]Checking APIs...[/dim]\n")
    results = asyncio.run(run_checks())

    for name, (is_healthy, message) in results.items():
        status = "[green]✓ Healthy[/green]" if is_healthy else "[red]✗ Unhealthy[/red]"
        console.print(f"  {name}: {status} - {message}")

    console.print()
    typer.prompt("Press Enter to continue")


def run_interactive_menu() -> None:
    """Run the main interactive menu loop."""
    config = RouterConfig()

    while True:
        console.clear()
        render_home_screen(config)
        render_menu_options()

        try:
            choice = Prompt.ask(
                "Select option",
                choices=["0", "1", "2", "3", "4", "5", "6"],
                default="1",
            )

            if choice == "0":
                console.print()
                print_info("Goodbye! 👋")
                console.print()
                sys.exit(0)
            elif choice == "1":
                handle_add_api(config)
            elif choice == "2":
                handle_list_apis(config)
            elif choice == "3":
                handle_manage_apis(config)
            elif choice == "4":
                count = config.reset_all_cooldowns()
                print_success(f"Cleared {count} cooldown(s).")
                typer.prompt("Press Enter to continue")
            elif choice == "5":
                handle_launch_claude(config)
            elif choice == "6":
                handle_health_check(config)

        except KeyboardInterrupt:
            console.print()
            print_info("Goodbye! 👋")
            console.print()
            sys.exit(0)
        except EOFError:
            sys.exit(0)
