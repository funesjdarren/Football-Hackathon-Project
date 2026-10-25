"""Repo-anchored paths, so the scripts work from any working directory."""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "football_data")
ENV_PATH = os.path.join(REPO_ROOT, ".env")
