from __future__ import annotations

from typing import Protocol

from ..graph_space.models import Node


class SchedulingPolicy(Protocol):
    def order(self, ready_nodes: tuple[Node, ...]) -> tuple[Node, ...]:
        ...


class FifoPolicy:
    name = "fifo"

    def order(self, ready_nodes: tuple[Node, ...]) -> tuple[Node, ...]:
        return tuple(sorted(ready_nodes, key=lambda node: node.created_order))
