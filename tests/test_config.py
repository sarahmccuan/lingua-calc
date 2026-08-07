from __future__ import annotations

import pytest

from lingua_calc.config import Settings

# Names other tools set routinely. None of them may reach a setting: an
# inherited MAX_WORKERS quietly changing Bedrock concurrency, or an inherited
# DB_PATH quietly repointing the token store, is a failure that still looks like
# a successful run.
GENERIC_ENV_VARS = {
    "DB_PATH": "/somewhere/else.sqlite3",
    "MAX_WORKERS": "99",
    "PORT": "1",
    "HOST": "0.0.0.0",
    "MAX_CHUNK_CHARS": "99999",
    "PERSIST_RUNS": "false",
    "DEBUG_TRACEBACKS": "true",
}


@pytest.fixture
def clean_env(monkeypatch):
    """Drop anything that would otherwise decide the values under test."""
    for name in (*GENERIC_ENV_VARS, *(f"LINGUA_{n}" for n in GENERIC_ENV_VARS)):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def settings_from_env(**kwargs) -> Settings:
    """Settings built from the environment alone.

    ``_env_file=None`` because these tests assert defaults, and the developer's
    own ``.env`` sits in the repo root — reading it would make whether they pass
    depend on a file that is deliberately not in version control.
    """
    return Settings(_env_file=None, **kwargs)


def test_unprefixed_environment_variables_do_not_reach_settings(clean_env):
    """The LINGUA_ prefix is the whole point — it has to actually be required."""
    for name, value in GENERIC_ENV_VARS.items():
        clean_env.setenv(name, value)

    settings = settings_from_env()

    assert settings.db_path == "data/lingua_calc.sqlite3"
    assert settings.max_workers == 8
    assert settings.port == 8765
    assert settings.host == "127.0.0.1"
    assert settings.max_chunk_chars == 1200
    assert settings.persist_runs is True
    assert settings.debug_tracebacks is False


def test_the_prefixed_name_is_what_configures_a_setting(clean_env):
    clean_env.setenv("LINGUA_DB_PATH", "/configured.sqlite3")
    clean_env.setenv("LINGUA_MAX_WORKERS", "3")

    settings = settings_from_env()

    assert settings.db_path == "/configured.sqlite3"
    assert settings.max_workers == 3


def test_the_prefixed_name_wins_over_a_generic_one(clean_env):
    clean_env.setenv("DB_PATH", "/generic.sqlite3")
    clean_env.setenv("LINGUA_DB_PATH", "/configured.sqlite3")

    assert settings_from_env().db_path == "/configured.sqlite3"


def test_keyword_construction_still_binds_by_field_name(clean_env):
    """`populate_by_name` was dropped because it re-exposed the unprefixed
    names. Prefix-derived fields bind a keyword without it — if one ever grows a
    validation_alias, this breaks and the `settings` fixture starts silently
    writing to the real database."""
    settings = settings_from_env(db_path="/explicit.sqlite3", max_workers=2, persist_runs=False)

    assert settings.db_path == "/explicit.sqlite3"
    assert settings.max_workers == 2
    assert settings.persist_runs is False


def test_credentials_keep_the_standard_aws_names(clean_env):
    """boto3 users already have these set, and prefixing them would strand a
    working environment. An explicit validation_alias opts them out."""
    clean_env.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    clean_env.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    clean_env.delenv("LINGUA_AWS_ACCESS_KEY_ID", raising=False)

    settings = settings_from_env()

    assert settings.aws_access_key_id == "AKIAEXAMPLE"
    assert settings.aws_region == "eu-west-1"
