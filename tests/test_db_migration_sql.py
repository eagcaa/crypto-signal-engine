import inspect
import re

from crypto_signal_engine.db import session


def test_plpgsql_blocks_use_valid_dollar_quoting() -> None:
    # PostgreSQL requires $$ (or $tag$) around DO bodies; a single "$" is a
    # syntax error that breaks initialize_database for every command.
    source = inspect.getsource(session)
    assert not re.search(r"\bDO \$\s*\n", source)
    assert not re.search(r"^\s*\$;\s*$", source, flags=re.MULTILINE)
    assert source.count("DO $$") == source.count("$$;")
