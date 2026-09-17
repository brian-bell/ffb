"""The CLI test seam renders assertable output in any color environment."""

from pathlib import Path

from ffb.cli import app

from .cli_plain import PlainCliRunner, strip_ansi

runner = PlainCliRunner()


def test_strip_ansi_drops_escapes_and_keeps_text():
    assert strip_ansi("\x1b[32mready\x1b[0m sleeper") == "ready sleeper"


def test_runner_output_is_ansi_free_even_when_color_is_forced(monkeypatch, tmp_path):
    """rich styles `[green]ready[/green]`; the assertion must not have to know."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    env = {"FFB_DB_PATH": str(tmp_path / "db"), "FORCE_COLOR": "1"}
    result = runner.invoke(app, ["season", "status", "2024"], env=env)
    assert "\x1b[" not in result.output
    assert "\x1b[" not in result.stdout


def test_bad_parameter_messages_survive_color(monkeypatch, tmp_path):
    """Typer renders usage errors through rich, wrapping flags in ANSI."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    env = {"FFB_DB_PATH": str(tmp_path / "db"), "FORCE_COLOR": "1"}
    result = runner.invoke(app, ["season", "sync", "2024", "--offline", "--refresh"], env=env)
    assert result.exit_code == 2
    assert "\x1b[" not in result.output
    assert "--refresh, or --offline" in result.output


def test_cli_tests_invoke_through_the_plain_runner():
    """A raw CliRunner brings ANSI back, so cli_plain owns that import."""
    needle = "from typer.testing " + "import"
    offenders = [
        path.name
        for path in sorted(Path(__file__).parent.rglob("*.py"))
        if path.name not in {"cli_plain.py", Path(__file__).name} and needle in path.read_text()
    ]
    assert offenders == [], f"use PlainCliRunner from tests.cli_plain: {offenders}"
