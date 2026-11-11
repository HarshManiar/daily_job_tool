"""Firsthand command-line interface."""

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Firsthand command-line interface."""


@app.command()
def version() -> None:
    """Print the Firsthand version."""
    typer.echo("0.1.0")
