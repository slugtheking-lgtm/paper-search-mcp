import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_search_mcp import config


class TestConfigEnv(unittest.TestCase):
    def test_project_env_files_are_default_candidates(self):
        with patch.dict(
            os.environ,
            {"PAPER_SEARCH_MCP_ENV_FILE": ""},
            clear=False,
        ):
            candidates = config._candidate_env_files()
        project_root = Path(config.__file__).resolve().parent.parent
        self.assertEqual(candidates[0], project_root / ".env")
        self.assertEqual(candidates[1], project_root / ".env.example")

    def test_prefixed_env_has_priority_over_legacy(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "prefixed-value",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "prefixed-value")

    def test_legacy_env_fallback_still_works(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "legacy-value")

    def test_empty_prefixed_value_blocks_legacy_fallback(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", "default"), "")

    def test_loads_from_custom_env_file(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text("PAPER_SEARCH_MCP_CORE_API_KEY=test-key\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(env_file),
                },
                clear=True,
            ):
                config.load_env_file(force=True)
                self.assertEqual(config.get_env("CORE_API_KEY", ""), "test-key")


if __name__ == "__main__":
    unittest.main()
