"""Configuration management for Ollama Router."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ApiEntry(BaseModel):
    """Represents a single Ollama API configuration."""

    name: str = Field(..., description="User-friendly name for this API key")
    api_key: str = Field(..., description="The Ollama API key (sk-ollama-account-...)")
    api_base: str = Field(default="https://ollama.com/api", description="API base URL")
    model_name: str = Field(..., description="The model to use (e.g., ollama-reasoner)")
    litellm_model: str = Field(
        default="", description="LiteLLM model string (e.g., ollama_chat/gemma-3-1b-reasoning)"
    )
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    cooldown_until: Optional[str] = Field(default=None, description="Cooldown expiry time")
    failed_count: int = Field(default=0, description="Number of consecutive failures")
    total_requests: int = Field(default=0, description="Total requests made with this key")
    is_active: bool = Field(default=True, description="Whether this API is enabled")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class RouterState(BaseModel):
    """Persistent state for the router."""

    current_index: int = Field(default=0, description="Index of current API in rotation")
    last_rotation: Optional[str] = Field(default=None, description="Last rotation timestamp")
    total_rotations: int = Field(default=0, description="Total number of rotations performed")


class RouterConfig:
    """Manages Ollama Router configuration with JSON persistence."""

    DEFAULT_CONFIG_DIR = Path.home() / ".ollama-router"
    DEFAULT_CONFIG_FILE = "config.json"
    DEFAULT_COOLDOWN_MINUTES = 5

    def __init__(self, config_dir: Optional[Path] = None, config_file: Optional[str] = None):
        """Initialize the configuration manager.

        Args:
            config_dir: Directory to store config (default: ~/.ollama-router)
            config_file: Config filename (default: config.json)
        """
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.config_file = config_file or self.DEFAULT_CONFIG_FILE
        self.config_path = self.config_dir / self.config_file

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load or initialize configuration
        self._apis: List[ApiEntry] = []
        self._state = RouterState()
        self._load()

    def _load(self) -> None:
        """Load configuration from disk."""
        if not self.config_path.exists():
            self._save()
            return

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)

            # Load API entries
            apis_data = data.get("apis", [])
            self._apis = [ApiEntry(**api) for api in apis_data]

            # Load state
            state_data = data.get("state", {})
            self._state = RouterState(**state_data) if state_data else RouterState()

        except (json.JSONDecodeError, Exception) as e:
            # Corrupted config - start fresh
            print(f"Warning: Config file corrupted, starting fresh: {e}")
            self._apis = []
            self._state = RouterState()
            self._save()

    def _save(self) -> None:
        """Save configuration to disk."""
        data = {
            "apis": [api.model_dump() for api in self._apis],
            "state": self._state.model_dump(),
            "version": "1.0",
            "updated_at": datetime.now().isoformat(),
        }

        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    # ==================== API Management ====================

    def add_api(
        self,
        name: str,
        api_key: str,
        model_name: str,
        api_base: str = "https://ollama.com/api",
        litellm_model: str = "",
    ) -> ApiEntry:
        """Add a new API configuration.

        Args:
            name: User-friendly name for this API
            api_key: The Ollama API key
            model_name: Model identifier (e.g., ollama-reasoner)
            api_base: API base URL (default: https://ollama.com/api)
            litellm_model: LiteLLM model string

        Returns:
            The created ApiEntry

        Raises:
            ValueError: If name already exists
        """
        # Check for duplicate name
        if any(api.name.lower() == name.lower() for api in self._apis):
            raise ValueError(f"An API with name '{name}' already exists")

        entry = ApiEntry(
            name=name,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            litellm_model=litellm_model,
        )

        self._apis.append(entry)
        self._save()
        return entry

    def remove_api(self, identifier: str) -> Optional[ApiEntry]:
        """Remove an API configuration by name or index.

        Args:
            identifier: API name or index (0-based)

        Returns:
            The removed ApiEntry, or None if not found
        """
        # Try by name first
        for i, api in enumerate(self._apis):
            if api.name.lower() == identifier.lower():
                self._apis.pop(i)
                self._save()
                return api

        # Try by index
        try:
            index = int(identifier)
            if 0 <= index < len(self._apis):
                return self._apis.pop(index)
        except ValueError:
            pass

        self._save()
        return None

    def get_api(self, identifier: str) -> Optional[ApiEntry]:
        """Get an API configuration by name or index.

        Args:
            identifier: API name or index (0-based)

        Returns:
            The ApiEntry, or None if not found
        """
        # Try by name first
        for api in self._apis:
            if api.name.lower() == identifier.lower():
                return api

        # Try by index
        try:
            index = int(identifier)
            if 0 <= index < len(self._apis):
                return self._apis[index]
        except ValueError:
            pass

        return None

    def list_apis(self) -> List[ApiEntry]:
        """List all configured API entries."""
        return self._apis.copy()

    def get_active_apis(self) -> List[ApiEntry]:
        """List all active (non-disabled) API entries."""
        return [api for api in self._apis if api.is_active]

    def update_api(self, identifier: str, **updates: Any) -> Optional[ApiEntry]:
        """Update an API configuration.

        Args:
            identifier: API name or index
            **updates: Fields to update

        Returns:
            The updated ApiEntry, or None if not found
        """
        api = self.get_api(identifier)
        if not api:
            return None

        for key, value in updates.items():
            if hasattr(api, key):
                setattr(api, key, value)

        self._save()
        return api

    # ==================== State Management ====================

    def get_current_index(self) -> int:
        """Get the current API index in rotation."""
        # Ensure index is valid
        if not self._apis:
            return 0
        return min(self._state.current_index, len(self._apis) - 1)

    def set_current_index(self, index: int) -> None:
        """Set the current API index."""
        if self._apis:
            self._state.current_index = max(0, min(index, len(self._apis) - 1))
            self._state.last_rotation = datetime.now().isoformat()
            self._save()

    def increment_rotation(self) -> None:
        """Increment the rotation counter and move to next API."""
        if self._apis:
            self._state.current_index = (self._state.current_index + 1) % len(self._apis)
            self._state.total_rotations += 1
            self._state.last_rotation = datetime.now().isoformat()
            self._save()

    def get_total_rotations(self) -> int:
        """Get total number of rotations performed."""
        return self._state.total_rotations

    # ==================== Cooldown Management ====================

    def set_cooldown(self, api_key: str, duration_minutes: Optional[int] = None) -> None:
        """Set a cooldown for an API key.

        Args:
            api_key: The API key to cool down
            duration_minutes: Cooldown duration (default: DEFAULT_COOLDOWN_MINUTES)
        """
        if duration_minutes is None:
            duration_minutes = self.DEFAULT_COOLDOWN_MINUTES

        for api in self._apis:
            if api.api_key == api_key:
                api.cooldown_until = (
                    datetime.now() + timedelta(minutes=duration_minutes)
                ).isoformat()
                api.failed_count += 1
                break

        self._save()

    def clear_cooldown(self, identifier: str) -> bool:
        """Clear cooldown for an API.

        Args:
            identifier: API name or index

        Returns:
            True if cooldown was cleared, False if not found
        """
        api = self.get_api(identifier)
        if api:
            api.cooldown_until = None
            api.failed_count = 0
            self._save()
            return True
        return False

    def is_on_cooldown(self, api: ApiEntry) -> bool:
        """Check if an API is currently on cooldown."""
        if not api.cooldown_until:
            return False

        try:
            cooldown_time = datetime.fromisoformat(api.cooldown_until)
            return cooldown_time > datetime.now()
        except (ValueError, TypeError):
            return False

    def get_available_api(self) -> Optional[ApiEntry]:
        """Get the next available API that is not on cooldown.

        Returns:
            An available ApiEntry, or None if all are on cooldown
        """
        now = datetime.now()

        for api in self._apis:
            if not api.is_active:
                continue

            if api.cooldown_until:
                try:
                    cooldown_time = datetime.fromisoformat(api.cooldown_until)
                    if cooldown_time > now:
                        continue  # Still on cooldown
                except (ValueError, TypeError):
                    pass  # Invalid cooldown time, treat as expired

            return api

        return None

    # ==================== Utility Methods ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get configuration statistics."""
        active_count = sum(1 for api in self._apis if api.is_active)
        cooldown_count = sum(1 for api in self._apis if self.is_on_cooldown(api))

        return {
            "total_apis": len(self._apis),
            "active_apis": active_count,
            "on_cooldown": cooldown_count,
            "exhausted": len(self._apis) - active_count - cooldown_count,
            "total_rotations": self._state.total_rotations,
            "current_index": self._state.current_index,
        }

    def reset_all_cooldowns(self) -> int:
        """Clear cooldowns for all APIs.

        Returns:
            Number of cooldowns cleared
        """
        count = 0
        for api in self._apis:
            if api.cooldown_until:
                api.cooldown_until = None
                count += 1
        self._save()
        return count

    def reset_stats(self) -> None:
        """Reset all statistics and cooldowns."""
        for api in self._apis:
            api.cooldown_until = None
            api.failed_count = 0
            api.total_requests = 0

        self._state = RouterState()
        self._save()
