"""Regression coverage for directory-only, multi-profile plugin packaging."""
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from pm.workspace import _workspace_member


REPO = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_same_plugin_in_two_profiles_has_unique_workspace_names(self):
        # Keep fixtures outside the plugin: workspace generation copies its source.
        with tempfile.TemporaryDirectory(prefix="cartesia-packaging-") as temporary:
            root = Path(temporary)
            names = []
            for profile in ("default", "Jack"):
                plugin = root / profile / "plugins" / "cartesia-voice"
                plugin.mkdir(parents=True)
                for filename in ("pyproject.toml", "plugin.yaml"):
                    shutil.copyfile(REPO / filename, plugin / filename)
                (plugin / "__init__.py").write_text("# Minimal native plugin fixture.\n")
                member = _workspace_member(plugin, root / "generation", identity=plugin)
                document = tomllib.loads((member / "pyproject.toml").read_text())
                names.append(document["project"]["name"])
            self.assertNotEqual(names[0], names[1], "Profiles must not duplicate uv workspace project names")

    def test_native_plugin_metadata_is_dependency_only(self):
        document = tomllib.loads((REPO / "pyproject.toml").read_text())
        self.assertNotIn("build-system", document)
        self.assertIs(document["tool"]["uv"]["package"], False)
        self.assertNotIn("setuptools", document["tool"])
        self.assertEqual(document["project"]["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
