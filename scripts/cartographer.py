#!/usr/bin/env python3
"""Living Codebase Cartographer — deterministic repository scanner (v2).

Technology-agnostic entry point. All analysis lives in modular analyzers
(`../analyzers/`); the engine lives in `core.py`. See SKILL.md and
`../analyzers/README.md` for the architecture.

Usage (run from the repository root):
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py init
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py sync
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py status
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py validate
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py detect
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py query --kind endpoint
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py impact ClaimService
  python3 .dsh/skills/living-codebase-cartographer/scripts/cartographer.py flow --from Fnol --to claim
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import main

if __name__ == "__main__":
    sys.exit(main())
