"""Unit tests for the intake request parser."""
from __future__ import annotations

import pytest

from job_star.context_gatherer.parser import (
    IntakeParser,
    ParsedRequest,
    parse_intake,
)


class TestIntakeParser:
    """Tests for parsing raw intake requests into structured form."""

    def test_parse_full_request(self, sample_intake_request: dict):
        """A well-formed request should parse into a ParsedRequest."""
        result = parse_intake(sample_intake_request)

        assert isinstance(result, ParsedRequest)
        assert result.id == "JOB-42"
        assert result.title == "Login fails on Safari when password contains '+'"
        assert "auth" in result.keywords
        assert result.file_hints == ["src/auth.py", "src/session.py"]
        assert result.error_signature == "ValueError: invalid cookie value"

    def test_parse_extracts_keywords_from_description(self):
        """Keywords should be extracted from description if not explicitly provided."""
        raw = {
            "id": "JOB-1",
            "title": "Crash on startup",
            "description": "The parser crashes when reading config.yaml on boot.",
        }
        result = parse_intake(raw)

        # Should extract meaningful tokens, not stopwords
        assert "parser" in result.keywords or "config" in result.keywords
        assert "the" not in result.keywords
        assert "on" not in result.keywords

    def test_parse_minimal_request(self):
        """A request with only an id and title should still parse."""
        raw = {"id": "JOB-7", "title": "Fix typo"}
        result = parse_intake(raw)

        assert result.id == "JOB-7"
        assert result.title == "Fix typo"
        assert result.keywords == []
        assert result.file_hints == []
        assert result.description == ""

    def test_parse_missing_id_raises(self):
        """A request without an id should raise ValueError."""
        with pytest.raises(ValueError, match="id"):
            parse_intake({"title": "No ID"})

    def test_parse_missing_title_raises(self):
        """A request without a title should raise ValueError."""
        with pytest.raises(ValueError, match="title"):
            parse_intake({"id": "JOB-1"})

    def test_parser_class_stateless(self):
        """The IntakeParser class should be reusable across calls."""
        parser = IntakeParser()
        r1 = parser.parse({"id": "A", "title": "First"})
        r2 = parser.parse({"id": "B", "title": "Second"})

        assert r1.id == "A"
        assert r2.id == "B"

    def test_parse_strips_whitespace(self):
        """Whitespace in fields should be stripped."""
        raw = {
            "id": "  JOB-9  ",
            "title": "  Spaced title  ",
            "description": "  Leading and trailing  ",
        }
        result = parse_intake(raw)

        assert result.id == "JOB-9"
        assert result.title == "Spaced title"

    def test_parse_file_hints_deduplicated(self):
        """Duplicate file hints should be deduplicated."""
        raw = {
            "id": "JOB-3",
            "title": "Dup hints",
            "file_hints": ["src/a.py", "src/a.py", "src/b.py"],
        }
        result = parse_intake(raw)

        assert result.file_hints == ["src/a.py", "src/b.py"]

    def test_parsed_request_to_dict_roundtrip(self, sample_intake_request: dict):
        """ParsedRequest should serialize back to a dict."""
        result = parse_intake(sample_intake_request)
        d = result.to_dict()

        assert d["id"] == sample_intake_request["id"]
        assert d["title"] == sample_intake_request["title"]
