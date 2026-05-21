from typing import Any, Dict, Optional


class AgentBase:
    """Simple base class for agents."""

    def __init__(self, name: str):
        self.name = name

    def act(self, payload: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform agent action and return a dict result."""
        raise NotImplementedError()
