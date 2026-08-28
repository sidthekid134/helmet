from pathlib import Path

import pytest

from helmet import _parser
from helmet.dev import run_dev


def test_dev_command_defaults() -> None:
    arguments = _parser().parse_args(["dev"])

    assert arguments.api_port == 8000
    assert arguments.web_port == 3000
    assert arguments.no_reload is False


def test_dev_command_accepts_port_and_reload_options() -> None:
    arguments = _parser().parse_args(
        ["dev", "--api-port", "8100", "--web-port", "3100", "--no-reload"]
    )

    assert arguments.api_port == 8100
    assert arguments.web_port == 3100
    assert arguments.no_reload is True


def test_dev_command_reports_missing_web_app(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Next.js app not found"):
        run_dev(project_root=tmp_path)
