"""
Unit tests for TodoManager in agent/loop.py

Tests: creation, status transitions, validation, rendering, has_open_items.
"""

import pytest
from loop import TodoManager


def make_item(content="Do something", status="pending", active="Doing something"):
    return {"content": content, "status": status, "activeForm": active}


class TestTodoManager:

    def setup_method(self):
        self.todo = TodoManager()

    def test_create_todos(self):
        """4.1: 3 items, all pending → render shows 3 [ ] items."""
        items = [make_item(f"Task {i}") for i in range(3)]
        result = self.todo.update(items)
        assert result.count("[ ]") == 3

    def test_mark_in_progress(self):
        """4.2: 1 item with in_progress → shows [>] with activeForm suffix."""
        items = [make_item("Run analysis", "in_progress", "Running analysis")]
        result = self.todo.update(items)
        assert "[>]" in result
        assert "Running analysis" in result

    def test_mark_completed(self):
        """4.3: 1 item completed out of 3 → shows [x], counter shows 1/3."""
        items = [
            make_item("Task 1", "completed", "Doing task 1"),
            make_item("Task 2"),
            make_item("Task 3"),
        ]
        result = self.todo.update(items)
        assert "[x]" in result
        assert "(1/3 completed)" in result

    def test_max_20_items(self):
        """4.4: 21 items → raises ValueError('Max 20 todos')."""
        items = [make_item(f"Task {i}") for i in range(21)]
        with pytest.raises(ValueError, match="Max 20 todos"):
            self.todo.update(items)

    def test_only_one_in_progress(self):
        """4.5: 2 items with in_progress → raises ValueError."""
        items = [
            make_item("Task 1", "in_progress", "Doing 1"),
            make_item("Task 2", "in_progress", "Doing 2"),
        ]
        with pytest.raises(ValueError, match="Only one in_progress"):
            self.todo.update(items)

    def test_missing_content_raises(self):
        """4.6: Item without content → raises ValueError('content required')."""
        items = [{"content": "", "status": "pending", "activeForm": "doing"}]
        with pytest.raises(ValueError, match="content required"):
            self.todo.update(items)

    def test_missing_active_form_raises(self):
        """4.7: Item without activeForm → raises ValueError('activeForm required')."""
        items = [{"content": "Do it", "status": "pending", "activeForm": ""}]
        with pytest.raises(ValueError, match="activeForm required"):
            self.todo.update(items)

    def test_invalid_status_raises(self):
        """4.8: status='invalid' → raises ValueError('invalid status')."""
        items = [make_item("Task", "invalid_status", "Doing")]
        with pytest.raises(ValueError, match="invalid status"):
            self.todo.update(items)

    def test_has_open_items_returns_true(self):
        """4.9: Mix of completed/pending → has_open_items() returns True."""
        items = [
            make_item("Task 1", "completed", "Done"),
            make_item("Task 2"),
        ]
        self.todo.update(items)
        assert self.todo.has_open_items() is True

    def test_has_open_items_all_done(self):
        """4.10: All completed → has_open_items() returns False."""
        items = [make_item(f"Task {i}", "completed", "Done") for i in range(3)]
        self.todo.update(items)
        assert self.todo.has_open_items() is False

    def test_empty_todo_has_no_open_items(self):
        """Empty TodoManager → has_open_items() returns False."""
        assert self.todo.has_open_items() is False
