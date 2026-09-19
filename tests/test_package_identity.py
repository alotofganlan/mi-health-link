import importlib
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_public_package_and_commands_use_mi_health_link_identity():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["name"] == "mi-health-link"
    assert set(project["scripts"]) == {
        "mi-health-link",
        "mi-health-link-s4",
        "mi-health-link-s4-normalize",
        "mi-health-link-s4-audit",
        "mi-health-link-auto-sync",
        "mi-health-link-nightscout-sync",
        "mi-health-link-blood-glucose",
        "mi-health-link-mcp",
    }

    for target in project["scripts"].values():
        module_name, function_name = target.split(":", 1)
        assert module_name.startswith("mi_health_link.")
        assert callable(getattr(importlib.import_module(module_name), function_name))


def test_installable_import_namespace_is_mi_health_link():
    assert (ROOT / "src/mi_health_link/__init__.py").is_file()
    assert not (ROOT / "src/xiaomi_health_sync").exists()
