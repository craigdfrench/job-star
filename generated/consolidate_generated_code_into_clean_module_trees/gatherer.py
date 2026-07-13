"""ContextGatherer: assembles a ContextBundle from registered sources."""

from __future__ import annotations

from typing import Iterable, Optional

from jobstar.context_gatherer.models import ContextBundle
from jobstar.context_gatherer.sources import SOURCES


class ContextGatherer:
    """Collects context from pluggable sources into a ContextBundle."""

    def __init__(self, source_names: Optional[Iterable[str]] = None) -> None:
        self.source_names = list(source_names) if source_names else list(SOURCES.keys())

    def gather(self, request_id: str, raw_input: str, history=None) -> ContextBundle:
        env: dict = {}
        metadata: dict = {}
        for name in self.source_names:
            fn = SOURCES.get(name)
            if fn is None:
                continue
            data = fn() or {}
            if name in ("env", "host", "runtime"):
                env.update(data)
            else:
                metadata[name] = data
        return ContextBundle(
            request_id=request_id,
            raw_input=raw_input,
            env=env,
            history=list(history or []),
            metadata=metadata,
        )
