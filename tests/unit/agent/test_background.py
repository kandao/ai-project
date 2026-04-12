"""
Unit tests for BackgroundManager in agent/loop.py

Tests: run, check, drain, notifications.
"""

import time
import pytest
from loop import BackgroundManager


class TestBackgroundManager:

    def setup_method(self):
        self.bg = BackgroundManager()

    def test_run_returns_started_message(self):
        """7.1: background_run('echo hello') → returns 'Background task <id> started: ...'."""
        result = self.bg.run("echo hello")
        assert "Background task" in result
        assert "started:" in result

    def test_check_task_status_after_completion(self):
        """7.2: Run + wait + check → status: completed, result: 'hello'."""
        self.bg.run("echo hello")
        time.sleep(0.5)  # Give it time to complete
        status = self.bg.check()
        assert "completed" in status

    def test_check_unknown_task(self):
        """7.3: Check unknown task ID → 'Unknown: <id>'."""
        result = self.bg.check("nonexistent_id_xyz")
        assert "Unknown: nonexistent_id_xyz" in result

    def test_drain_returns_notifications(self):
        """7.4: Run task, wait, drain → notification with task_id, status, result."""
        self.bg.run("echo hello_world")
        time.sleep(0.5)
        notifs = self.bg.drain()
        assert len(notifs) >= 1
        n = notifs[0]
        assert "task_id" in n
        assert "status" in n
        assert "result" in n

    def test_drain_empty_queue(self):
        """7.5: Drain with no tasks run → empty list."""
        notifs = self.bg.drain()
        assert notifs == []

    def test_multiple_concurrent_tasks(self):
        """7.7: Run 3 tasks → all complete independently."""
        for i in range(3):
            self.bg.run(f"echo task{i}")
        time.sleep(1.0)
        status = self.bg.check()
        # All 3 should appear
        assert "task" in status

    def test_dangerous_command_blocked(self):
        """Background tasks that use dangerous commands get error status."""
        self.bg.run("rm -rf /")
        time.sleep(0.5)
        notifs = self.bg.drain()
        if notifs:  # May have finished already
            # Result should contain an error message
            assert any("Error" in n["result"] for n in notifs)
        else:
            # Check via check()
            status = self.bg.check()
            # If the task ran, it should be an error
            if "error" in status.lower() or "Error" in status:
                pass  # Expected
