"""Tests for the SQL migration statement splitter in job_star.upgrade.

The splitter must:
  - strip leading blank lines and full-line -- comments from each statement so
    callers' startswith() checks work even when a statement is preceded by a
    comment block (regression test for the silent-skip bug where a statement
    like "-- comment\nCREATE TABLE ..." was dropped).
  - preserve inline comments (after SQL on the same line) and mid-statement
    comment lines.
  - respect $$ dollar-quote blocks (no splitting on semicolons inside them).
"""

import pytest

from job_star.upgrade import _split_sql_statements


def test_leading_comments_stripped_before_statement():
    """A statement preceded by comment lines must start with SQL, not '--'."""
    sql = "-- comment one\n-- comment two\n\nCREATE TABLE foo (id INT);\n"
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 1
    assert stmts[0].startswith("CREATE TABLE foo")
    assert not stmts[0].startswith("--")


def test_create_table_detection_works_with_leading_comments():
    """The apply_migrations path uses upper().startswith('CREATE TABLE ...')."""
    sql = "-- header comment\nCREATE TABLE IF NOT EXISTS bar (id SERIAL PRIMARY KEY);\n"
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 1
    assert stmts[0].upper().startswith("CREATE TABLE IF NOT EXISTS")


def test_inline_comment_preserved():
    """Inline comments (after SQL on the same line) are kept."""
    sql = "CREATE TABLE foo (id INT);  -- inline comment\n"
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 1
    assert "inline comment" in stmts[0]


def test_mid_statement_comment_preserved_leading_stripped():
    """Comment lines inside a multi-line statement are kept; only leading ones stripped."""
    sql = "INSERT INTO t (a)\n-- mid comment\nVALUES (1);\n"
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 1
    assert stmts[0].startswith("INSERT INTO t")
    assert "mid comment" in stmts[0]


def test_multiple_statements_each_with_leading_comments():
    """Every statement in a sequence gets its own leading comments stripped."""
    sql = (
        "-- a\nCREATE TABLE x (id INT);\n"
        "-- b\nCREATE TABLE y (id INT);\n"
        "-- c\nCREATE INDEX i ON x (id);\n"
    )
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 3
    assert all(not s.startswith("--") for s in stmts)
    assert stmts[0].startswith("CREATE TABLE x")
    assert stmts[1].startswith("CREATE TABLE y")
    assert stmts[2].startswith("CREATE INDEX i")


def test_dollar_quote_block_not_split_on_internal_semicolon():
    """Semicolons inside $$ ... $$ blocks must not split the statement."""
    sql = (
        "CREATE OR REPLACE FUNCTION f()\n"
        "RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "  NEW.x = 1;\n"
        "  RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
    )
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    # The function definition is one statement
    assert len(stmts) == 1
    assert "LANGUAGE plpgsql" in stmts[0]


def test_dollar_quote_closing_shares_line_with_text():
    """A closing $$ that shares a line with other text (e.g. "$$ LANGUAGE
    plpgsql;") must not split the function body, and the following statement
    must be separate. Regression test for Vikunja #1309: the old per-line $$
    count toggled state off prematurely, splitting at the internal END;."""
    sql = (
        "CREATE OR REPLACE FUNCTION f()\n"
        "RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "  NEW.x = 1;\n"
        "  RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n\n"
        "CREATE TRIGGER foo_updated BEFORE UPDATE ON foo\n"
        "    FOR EACH ROW EXECUTE FUNCTION f();\n"
    )
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    assert len(stmts) == 2, f"expected 2 statements, got {len(stmts)}"
    assert "LANGUAGE plpgsql" in stmts[0]
    assert "CREATE TRIGGER" in stmts[1]
    # The internal END; must not have split the function
    assert "END;" in stmts[0]
    assert "RETURN NEW;" in stmts[0]


def test_empty_and_comment_only_input():
    """Comment-only or empty input yields no runnable statements after stripping."""
    # A comment with no following ;-terminated statement becomes empty after
    # leading-comment stripping (the comment is the whole "statement").
    assert _split_sql_statements("-- just a comment\n") == [""]
    # Empty input: "".split("\n") == [''], so the splitter yields [''].
    # Callers filter falsy strings, so this is harmless.
    assert _split_sql_statements("") == [""]


def test_migration_003_parses_all_statements():
    """The real migration 003 file must produce 6 runnable statements (no skips)."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "sql", "migrations", "003_step_attempt_tracking.sql")
    if not os.path.exists(path):
        pytest.skip("migration 003 not present")
    sql = open(path).read()
    stmts = [s.strip() for s in _split_sql_statements(sql) if s.strip()]
    runnable = [s for s in stmts if not s.startswith("--")]
    # ALTER + 2 UPDATEs + 3 CREATE INDEX = 6
    assert len(runnable) == 6
    assert runnable[0].startswith("ALTER TABLE goal_steps")
    assert runnable[1].startswith("UPDATE goal_steps")
    assert runnable[2].startswith("UPDATE goal_steps")
    assert "CREATE INDEX" in runnable[3]
