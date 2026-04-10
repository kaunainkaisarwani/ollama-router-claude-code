"""API Rotation engine for Ollama Router."""

import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import ApiEntry, RouterConfig


class RotationError(Exception):
    """Exception raised when rotation fails."""

    pass


class AllApisExhaustedError(RotationError):
    """Exception raised when all APIs are on cooldown."""

    pass


class ApiRotator:
    """Manages round-robin rotation of Ollama API keys.

    Features:
    - Round-robin rotation through all configured APIs
    - Automatic cooldown on rate limit errors
    - Wrap-around to first API after last one
    - Persistent state across sessions
    """

    # Rate limit error patterns
    RATE_LIMIT_PATTERNS = [
        r"429",
        r"rate limit",
        r"too many requests",
        r"session limit",
        r"quota exceeded",
        r"quota exhausted",
        r"maximum.*reached",
        r"try again later",
        r"cooldown",
        r"throttl",
    ]

    # Cooldown durations based on error type
    COOLDOWN_SHORT = 2  # minutes - for minor rate limits
    COOLDOWN_MEDIUM = 5  # minutes - for standard rate limits
    COOLDOWN_LONG = 15  # minutes - for session limits
    COOLDOWN_VERY_LONG = 60  # minutes - for quota exhausted

    def __init__(self, config: Optional[RouterConfig] = None):
        """Initialize the API rotator.

        Args:
            config: RouterConfig instance (creates default if None)
        """
        self.config = config or RouterConfig()
        self._current_api: Optional[ApiEntry] = None
        self._rotation_lock = asyncio.Lock()

    def _detect_error_type(self, error_message: str) -> str:
        """Detect the type of error from the message.

        Args:
            error_message: The error message to analyze

        Returns:
            Error type classification
        """
        error_lower = error_message.lower()

        # Check for quota exhausted (most severe)
        if any(pattern in error_lower for pattern in ["quota exhausted", "quota exceeded"]):
            return "quota_exhausted"

        # Check for session limit
        if any(pattern in error_lower for pattern in ["session limit", "session expired"]):
            return "session_limit"

        # Check for rate limit
        if any(pattern in error_lower for pattern in ["rate limit", "429", "too many requests"]):
            return "rate_limit"

        # Check for temporary issues
        if any(
            pattern in error_lower
            for pattern in ["timeout", "connection", "temporary", "service unavailable"]
        ):
            return "temporary"

        return "unknown"

    def _get_cooldown_duration(self, error_type: str) -> int:
        """Get cooldown duration based on error type.

        Args:
            error_type: The classified error type

        Returns:
            Cooldown duration in minutes
        """
        cooldown_map = {
            "quota_exhausted": self.COOLDOWN_VERY_LONG,
            "session_limit": self.COOLDOWN_LONG,
            "rate_limit": self.COOLDOWN_MEDIUM,
            "temporary": self.COOLDOWN_SHORT,
            "unknown": self.COOLDOWN_MEDIUM,
        }
        return cooldown_map.get(error_type, self.COOLDOWN_MEDIUM)

    def is_rate_limit_error(self, error: Any) -> bool:
        """Check if an error indicates a rate limit.

        Args:
            error: Exception or error message to check

        Returns:
            True if this is a rate limit error
        """
        if error is None:
            return False

        # Handle exception objects
        if hasattr(error, "status_code"):
            if getattr(error, "status_code", None) == 429:
                return True

        # Handle string messages
        error_message = str(error).lower()

        for pattern in self.RATE_LIMIT_PATTERNS:
            if re.search(pattern, error_message, re.IGNORECASE):
                return True

        return False

    def get_cooldown_duration(self, error: Any) -> int:
        """Get appropriate cooldown duration for an error.

        Args:
            error: Exception or error message

        Returns:
            Cooldown duration in minutes
        """
        error_message = str(error)
        error_type = self._detect_error_type(error_message)
        return self._get_cooldown_duration(error_type)

    # ==================== Core lock-free rotation logic ====================

    def _get_next_api_unlocked(self) -> ApiEntry:
        """Get the next available API. MUST be called with _rotation_lock held.

        This is the internal, lock-free version used by public methods that
        already hold the lock, preventing deadlocks.

        Returns:
            The next available ApiEntry

        Raises:
            RotationError: If no APIs configured
            AllApisExhaustedError: If all APIs are on cooldown
        """
        apis = self.config.get_active_apis()

        if not apis:
            raise RotationError("No APIs configured. Use 'ollama-router add' to add APIs.")

        # Try to find an available API starting from current index
        start_index = self.config.get_current_index()

        for i in range(len(apis)):
            check_index = (start_index + i) % len(apis)
            api = apis[check_index]

            if not self.config.is_on_cooldown(api):
                # Found available API
                self._current_api = api
                self.config.set_current_index(check_index)
                return api

        # All APIs are on cooldown - check if any cooldowns have expired
        now = datetime.now()
        for api in apis:
            if api.cooldown_until:
                try:
                    cooldown_time = datetime.fromisoformat(api.cooldown_until)
                    if cooldown_time <= now:
                        # Cooldown expired, clear it
                        self.config.clear_cooldown(api.name)
                        self._current_api = api
                        self.config.set_current_index(apis.index(api))
                        return api
                except (ValueError, TypeError):
                    # Invalid cooldown, clear it
                    self.config.clear_cooldown(api.name)
                    self._current_api = api
                    self.config.set_current_index(apis.index(api))
                    return api

        # All APIs truly exhausted
        raise AllApisExhaustedError(
            f"All {len(apis)} APIs are on cooldown. "
            f"Wait for cooldowns to expire or use 'ollama-router reset-cooldowns'"
        )

    # ==================== Public API (thread-safe) ====================

    async def get_next_api(self, force_rotate: bool = False) -> ApiEntry:
        """Get the next available API key.

        Args:
            force_rotate: If True, skip current API and get next one

        Returns:
            The next available ApiEntry

        Raises:
            AllApisExhaustedError: If all APIs are on cooldown
        """
        async with self._rotation_lock:
            if force_rotate and self._current_api:
                self.config.increment_rotation()

            return self._get_next_api_unlocked()

    async def trigger_rotation(self, reason: str) -> ApiEntry:
        """Notify the rotator that the current API has failed and a rotation is needed.

        Marks the current API as failed, sets cooldown, increments rotation,
        and returns the next available API.

        Args:
            reason: Human-readable reason for the rotation

        Returns:
            The next available ApiEntry
        """
        async with self._rotation_lock:
            if self._current_api:
                await self.mark_failed(self._current_api.api_key, reason)

            self.config.increment_rotation()
            return self._get_next_api_unlocked()

    async def rotate_and_get_next(self, error: Optional[Any] = None) -> ApiEntry:
        """Mark current API as failed (if error) and get next available.

        Args:
            error: Optional error that triggered rotation

        Returns:
            The next available ApiEntry

        Raises:
            AllApisExhaustedError: If all APIs are exhausted
        """
        async with self._rotation_lock:
            # Mark current API as failed if there was an error
            if error and self._current_api:
                await self.mark_failed(self._current_api.api_key, error)

            # Force rotation to next API
            self.config.increment_rotation()

            # Get next available API (lock already held)
            return self._get_next_api_unlocked()

    # ==================== API Status Management ====================

    async def mark_failed(self, api_key: str, error: Optional[Any] = None) -> None:
        """Mark an API as failed and set cooldown.

        Args:
            api_key: The API key that failed
            error: The error that caused failure (optional)
        """
        # Find the API and mark it
        for api in self.config.list_apis():
            if api.api_key == api_key:
                cooldown_minutes = (
                    self.get_cooldown_duration(error) if error else self.COOLDOWN_MEDIUM
                )
                self.config.set_cooldown(api_key, cooldown_minutes)

                # Log the failure
                from .utils import print_warning

                print_warning(
                    f"API '{api.name}' failed. Setting {cooldown_minutes}min cooldown. "
                    f"Failures: {api.failed_count + 1}"
                )
                break

    async def mark_success(self, api_key: str) -> None:
        """Mark an API call as successful.

        Args:
            api_key: The API key that succeeded
        """
        for api in self.config.list_apis():
            if api.api_key == api_key:
                # Clear any existing cooldown
                if api.cooldown_until:
                    self.config.clear_cooldown(api.name)

                # Increment request counter
                api.total_requests += 1
                self.config._save()
                break

    def get_current_api(self) -> Optional[ApiEntry]:
        """Get the currently active API.

        Returns:
            The current ApiEntry, or None if not set
        """
        return self._current_api

    def get_rotation_stats(self) -> Dict[str, Any]:
        """Get rotation statistics.

        Returns:
            Dictionary with rotation statistics
        """
        apis = self.config.list_apis()
        config_stats = self.config.get_stats()

        # Add per-API details
        api_details = []
        for api in apis:
            api_details.append(
                {
                    "name": api.name,
                    "status": (
                        "COOLDOWN"
                        if self.config.is_on_cooldown(api)
                        else "ACTIVE" if api.is_active else "DISABLED"
                    ),
                    "total_requests": api.total_requests,
                    "failed_count": api.failed_count,
                    "model": api.model_name,
                }
            )

        return {
            **config_stats,
            "current_api": self._current_api.name if self._current_api else "None",
            "apis": api_details,
        }

    # ==================== Health Checks ====================

    async def health_check(self, api: ApiEntry, timeout: int = 10) -> Tuple[bool, str]:
        """Perform a health check on an API.

        Args:
            api: The API to check
            timeout: Request timeout in seconds

        Returns:
            Tuple of (is_healthy, error_message)
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Try a simple request to check API validity
                url = f"{api.api_base}/tags"
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api.api_key}"},
                )
                if response.status_code == 200:
                    return True, "OK"
                elif response.status_code == 401:
                    return False, "Invalid API key"
                elif response.status_code == 429:
                    return False, "Rate limited"
                else:
                    return False, f"Status {response.status_code}"

        except httpx.TimeoutException:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)

    async def check_all_apis(self) -> Dict[str, Tuple[bool, str]]:
        """Check health of all configured APIs.

        Returns:
            Dictionary mapping API names to (is_healthy, message) tuples
        """
        results = {}

        async def check_one(api: ApiEntry):
            is_healthy, message = await self.health_check(api)
            results[api.name] = (is_healthy, message)

        apis = self.config.list_apis()
        if apis:
            await asyncio.gather(*[check_one(api) for api in apis])

        return results
