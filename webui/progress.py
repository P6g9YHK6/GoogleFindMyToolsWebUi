"""Shared progress-callback shape for the long-running installers
(webui/browser_stack.py, webui/esp_idf_provisioning.py):
(phase, message, percent) -> None, awaited after each step so an SSE/htmx
progress bar can render it. _no_progress is the default for a caller (e.g.
the CLI or a test) that doesn't want one.
"""

from collections.abc import Awaitable, Callable

ProgressCallback = Callable[[str, str, int], Awaitable[None]]


async def _no_progress(phase: str, message: str, percent: int):
    pass
