#!/usr/bin/env python
from __future__ import annotations

import sys

from openai_vectorstore2.evals.open_ragbench import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
