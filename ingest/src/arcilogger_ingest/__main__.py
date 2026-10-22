"""Allow ``python -m arcilogger_ingest``.

The guard matters: worker processes of the parse pool are started with ``spawn`` (the only
method on Windows) and import the main module again; without it every worker would start
another ingest service.
"""

from arcilogger_ingest.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
