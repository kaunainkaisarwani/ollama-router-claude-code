"""Utility functions for colors, prompts, and formatting."""

from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.style import Style
from rich.text import Text

console = Console()

# Color styles
STYLE_ACTIVE = Style(color="green", bold=True)
STYLE_COOLDOWN = Style(color="yellow", bold=True)
STYLE_EXHAUSTED = Style(color="red", bold=True)
STYLE_DIM = Style(color="bright_black")
STYLE_HEADER = Style(color="cyan", bold=True)
STYLE_SUCCESS = Style(color="green", bold=True)
STYLE_ERROR = Style(color="red", bold=True)
STYLE_WARNING = Style(color="yellow", bold=True)


def print_header(text: str) -> None:
    """Print a styled header."""
    console.print()
    console.print(f"━" * console.width, style=STYLE_HEADER)
    console.print(f"  {text}", style=STYLE_HEADER)
    console.print(f"━" * console.width, style=STYLE_HEADER)
    console.print()


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"✓ {message}", style=STYLE_SUCCESS)


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"✗ {message}", style=STYLE_ERROR)


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"⚠ {message}", style=STYLE_WARNING)


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"ℹ {message}", style=STYLE_DIM)


def mask_api_key(key: str) -> str:
    """Mask an API key for display, showing only first and last 4 chars."""
    if len(key) <= 12:
        return "***" + key[-3:] if len(key) > 3 else "***"
    return key[:6] + "..." + key[-4:]


def format_cooldown_time(expires_at: Optional[datetime]) -> str:
    """Format cooldown expiry time."""
    if expires_at is None:
        return "N/A"

    # Handle string input (ISO format)
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except (ValueError, TypeError):
            return "N/A"

    now = datetime.now()
    if expires_at <= now:
        return "Expired"

    diff = expires_at - now
    minutes = int(diff.total_seconds() // 60)
    seconds = int(diff.total_seconds() % 60)

    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def get_status_style(api_entry: dict) -> Style:
    """Get the appropriate style for an API entry based on its status."""
    cooldown_until = api_entry.get("cooldown_until")

    if cooldown_until:
        try:
            cooldown_time = datetime.fromisoformat(cooldown_until)
            if cooldown_time > datetime.now():
                return STYLE_COOLDOWN
        except (ValueError, TypeError):
            pass

    if api_entry.get("failed_count", 0) >= 5:
        return STYLE_EXHAUSTED

    return STYLE_ACTIVE


def get_status_label(api_entry: dict) -> str:
    """Get the status label for an API entry."""
    cooldown_until = api_entry.get("cooldown_until")

    if cooldown_until:
        try:
            cooldown_time = datetime.fromisoformat(cooldown_until)
            if cooldown_time > datetime.now():
                remaining = format_cooldown_time(cooldown_time)
                return f"COOLDOWN ({remaining})"
        except (ValueError, TypeError):
            pass

    if api_entry.get("failed_count", 0) >= 5:
        return "EXHAUSTED"

    return "ACTIVE"


def truncate_middle(text: str, max_length: int = 30) -> str:
    """Truncate text in the middle with ellipsis."""
    if len(text) <= max_length:
        return text

    half = (max_length - 3) // 2
    return text[:half] + "..." + text[-half:]
