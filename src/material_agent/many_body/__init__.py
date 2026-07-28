"""Agent04 domain-only contracts.

This package deliberately has no solver, backend, runner, or Orchestrator
integration.  It contains only the versioned input/output boundary for the
many-body controller.
"""

from .models import *
from .evidence import *
from .registry import *
from .routing import *
from .mock_backend import *
