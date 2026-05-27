try:
    from app.graph.graph_runtime_agent import GraphAgent
except ModuleNotFoundError:  # pragma: no cover - optional runtime deps may be absent in tests
    GraphAgent = None  # type: ignore[assignment]

__all__ = [
    "GraphAgent",
]
