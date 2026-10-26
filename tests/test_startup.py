import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("paperstudio_startup", Path(__file__).parents[1] / "run.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def test_env_is_literal_and_real_environment_wins(tmp_path):
    marker = tmp_path / "must-not-exist"
    config = tmp_path / ".env"
    config.write_text(
        f"SECRET='$(touch {marker}) `touch {marker}` $NOT_EXPANDED'\n"
        "MODEL=file-value\nEMPTY=\nHASH=abc#def\nQUOTED=\"a # b\" # comment\n"
        "export WITH_SPACE=literal value # comment\n",
        encoding="utf-8",
    )
    environment = {"MODEL": "process-value"}
    assert startup.load_env_file(config, environment) == 6
    assert environment["MODEL"] == "process-value"
    assert environment["SECRET"].startswith("$(touch ")
    assert "$NOT_EXPANDED" in environment["SECRET"]
    assert environment["EMPTY"] == ""
    assert environment["HASH"] == "abc#def"
    assert environment["QUOTED"] == "a # b"
    assert environment["WITH_SPACE"] == "literal value"
    assert not marker.exists()


@pytest.mark.parametrize("text", ["MISSING_EQUALS", "BAD-KEY=secret", "KEY='unterminated", "KEY='value' unexpected"])
def test_invalid_env_does_not_print_secrets(tmp_path, text):
    config = tmp_path / ".env"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        startup.load_env_file(config, {})
    assert "line 1" in str(failure.value)
    assert "secret" not in str(failure.value)
    assert "unterminated" not in str(failure.value)


def test_check_config_does_not_start_app(tmp_path, monkeypatch, capsys):
    config = tmp_path / "settings.env"
    config.write_text("AI_API_KEY=do-not-print\nPAPERSTUDIO_PORT=8127\n", encoding="utf-8")
    monkeypatch.delenv("PAPERSTUDIO_PORT", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    startup.main(["--env-file", str(config), "--check-config"])
    output = capsys.readouterr()
    assert "No services started" in output.out
    assert "do-not-print" not in output.out + output.err
