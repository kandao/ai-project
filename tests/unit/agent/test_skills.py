"""
Unit tests for SkillLoader in agent/loop.py

Tests: skill parsing, descriptions, load by name, error handling.
"""

import tempfile
import os
from pathlib import Path

import pytest
from loop import SkillLoader


def make_skills_dir(skills: dict) -> Path:
    """Create a temp directory with SKILL.md files. Returns path."""
    tmp = tempfile.mkdtemp()
    for skill_name, content in skills.items():
        skill_dir = Path(tmp) / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content)
    return Path(tmp)


ANALYST_SKILL = """---
name: analyst
description: Business analysis expert
---
You are a business analyst. Use data to drive insights.
Focus on ROI, KPIs, and executive summaries.
"""

WRITER_SKILL = """---
name: writer
description: Technical writing expert
---
You write clear, concise documentation.
Prefer active voice and short sentences.
"""

NO_FRONTMATTER_SKILL = """This skill has no frontmatter.
Just raw body content."""


class TestSkillLoader:

    def test_load_skill_from_directory(self):
        """5.1: skills/analyst/SKILL.md exists → skill parsed with name and description."""
        tmp = make_skills_dir({"analyst": ANALYST_SKILL})
        loader = SkillLoader(tmp)
        assert "analyst" in loader.skills
        assert loader.skills["analyst"]["meta"]["description"] == "Business analysis expert"

    def test_descriptions_lists_all_skills(self):
        """5.2: Multiple skills → descriptions() lists all skill names and descriptions."""
        tmp = make_skills_dir({"analyst": ANALYST_SKILL, "writer": WRITER_SKILL})
        loader = SkillLoader(tmp)
        desc = loader.descriptions()
        assert "analyst" in desc
        assert "writer" in desc
        assert "Business analysis expert" in desc
        assert "Technical writing expert" in desc

    def test_load_unknown_skill_returns_error(self):
        """5.3: load('nonexistent') → returns 'Error: Unknown skill...' with available list."""
        tmp = make_skills_dir({"analyst": ANALYST_SKILL})
        loader = SkillLoader(tmp)
        result = loader.load("nonexistent")
        assert "Error: Unknown skill" in result
        assert "analyst" in result

    def test_yaml_frontmatter_parsed(self):
        """5.4: Skill with name: and description: → correctly parsed metadata."""
        tmp = make_skills_dir({"analyst": ANALYST_SKILL})
        loader = SkillLoader(tmp)
        assert loader.skills["analyst"]["meta"]["name"] == "analyst"
        assert loader.skills["analyst"]["meta"]["description"] == "Business analysis expert"

    def test_empty_skills_directory(self):
        """5.5: No SKILL.md files → descriptions() returns '(no skills loaded)'."""
        tmp = tempfile.mkdtemp()
        loader = SkillLoader(Path(tmp))
        assert loader.descriptions() == "(no skills loaded)"

    def test_skill_body_injected_in_tags(self):
        """5.6: load('analyst') → returns <skill name='analyst'>...</skill>."""
        tmp = make_skills_dir({"analyst": ANALYST_SKILL})
        loader = SkillLoader(tmp)
        result = loader.load("analyst")
        assert result.startswith('<skill name="analyst">')
        assert "Business analysis expert" not in result  # body, not meta
        assert "ROI" in result or "insights" in result  # body content present
        assert "</skill>" in result

    def test_skill_without_frontmatter_uses_dir_name(self):
        """Skill without frontmatter → uses parent directory name as skill name."""
        tmp = make_skills_dir({"myskill": NO_FRONTMATTER_SKILL})
        loader = SkillLoader(Path(tmp))
        assert "myskill" in loader.skills
