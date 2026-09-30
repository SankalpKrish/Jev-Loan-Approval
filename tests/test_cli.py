import sys
from pathlib import Path

import pytest

from jevloan import cli


@pytest.fixture
def fake_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway import root, so tests can define command modules that exist or don't."""
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.startswith("w1core_fake")]:
        del sys.modules[name]


def make(root: Path, dotted: str, source: str) -> None:
    *parents, leaf = dotted.split(".")
    directory = root
    for part in parents:
        directory = directory / part
        directory.mkdir(exist_ok=True)
        (directory / "__init__.py").touch()
    (directory / f"{leaf}.py").write_text(source, encoding="utf-8")


REGISTER = """
def register(subparsers):
    p = subparsers.add_parser("fake")
    p.add_argument("--code", type=int, default=0)
    p.set_defaults(func=lambda args: args.code)
"""


def test_registered_module_runs_and_exit_code_is_returned(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_ok.commands", REGISTER)
    monkeypatch.setattr(cli, "COMMAND_MODULES", ["w1core_fake_ok.commands"])
    assert cli.main(["fake"]) == 0
    assert cli.main(["fake", "--code", "3"]) == 3


def test_none_return_means_exit_0(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_none.commands", REGISTER.replace("args.code", "None"))
    monkeypatch.setattr(cli, "COMMAND_MODULES", ["w1core_fake_none.commands"])
    assert cli.main(["fake"]) == 0


def test_missing_modules_and_missing_parent_packages_are_skipped(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_present.commands", REGISTER)
    make(fake_modules, "w1core_fake_nocmd.other", "")
    monkeypatch.setattr(
        cli,
        "COMMAND_MODULES",
        [
            "w1core_fake_absent.commands",  # missing parent package
            "w1core_fake_absent.sub.commands",  # missing parent of parent
            "w1core_fake_nocmd.commands",  # parent exists, module doesn't
            "w1core_fake_present.commands",
        ],
    )
    assert cli.main(["fake"]) == 0


def test_import_error_in_an_existing_module_propagates(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_broken.commands", "import w1core_fake_not_installed_dep\n" + REGISTER)
    monkeypatch.setattr(cli, "COMMAND_MODULES", ["w1core_fake_broken.commands"])
    with pytest.raises(ModuleNotFoundError, match="w1core_fake_not_installed_dep"):
        cli.main(["fake"])


def test_import_error_in_a_parent_package_propagates(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_badparent.commands", REGISTER)
    (fake_modules / "w1core_fake_badparent" / "__init__.py").write_text("import w1core_fake_not_installed_dep\n")
    monkeypatch.setattr(cli, "COMMAND_MODULES", ["w1core_fake_badparent.commands"])
    with pytest.raises(ModuleNotFoundError, match="w1core_fake_not_installed_dep"):
        cli.main(["fake"])


def test_syntax_error_in_an_existing_module_propagates(fake_modules: Path, monkeypatch: pytest.MonkeyPatch):
    make(fake_modules, "w1core_fake_syntax.commands", "def register(:\n")
    monkeypatch.setattr(cli, "COMMAND_MODULES", ["w1core_fake_syntax.commands"])
    with pytest.raises(SyntaxError):
        cli.main(["fake"])


def test_command_module_list_matches_the_plan():
    assert cli.COMMAND_MODULES == [
        "jevloan.audit.commands",
        "jevloan.jev.commands",
        "jevloan.pii.commands",
        "jevloan.data.commands",
        "jevloan.state.commands",
        "jevloan.modules.commands",
        "jevloan.pipeline.commands",
        "jevloan.eval.commands",
        "jevloan.api.commands",
        "jevloan.monitors.commands",
    ]


def test_group_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
