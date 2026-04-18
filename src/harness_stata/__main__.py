"""Enable ``python -m harness_stata ...`` as an alias for the typer app."""

from harness_stata.cli import app

if __name__ == "__main__":
    app()
