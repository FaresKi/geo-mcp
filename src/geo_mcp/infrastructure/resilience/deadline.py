"""Deprecated shim — Deadline lives in domain. Prefer geo_mcp.domain.deadline."""

from geo_mcp.domain.deadline import Deadline, DeadlineExceeded

__all__ = ["Deadline", "DeadlineExceeded"]
