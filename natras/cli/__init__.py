"""CLI package."""

def main():
    from natras.cli.main import main as _main
    _main()

__all__ = ["main"]
