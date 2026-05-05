"""While-loop iteration engine primitives."""

from dataclasses import dataclass


@dataclass
class IterationPolicy:
    """Controls loop safety for iterative agent execution."""

    max_iterations: int = 120

