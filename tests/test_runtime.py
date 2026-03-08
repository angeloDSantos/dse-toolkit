import os
import sys
import json
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtime

def test_write_read_state():
    tool = "test_tool_1"
    runtime.write_tool_state(tool, status="starting", custom_field="test_value")
    
    state = runtime.read_tool_state(tool)
    assert state["status"] == "starting"
    assert state["custom_field"] == "test_value"
    assert "updated_at" in state

def test_launch_creates_log():
    tool = "test_tool_echo"
    proc, log_path = runtime.launch_tool(tool, ["python", "-c", "print('hello from subprocess')"])
    proc.wait(timeout=5)
    
    state = runtime.read_tool_state(tool)
    assert state["status"] == "running" # State remains running until actively stopped by app
    assert state["log_path"] == log_path
    
    # Check log content
    assert os.path.exists(log_path)
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "hello from subprocess" in content

def test_tail_log():
    tool = "test_tool_tail"
    proc, log_path = runtime.launch_tool(tool, ["python", "-c", "import sys; print('line1'); print('line2'); print('line3')"])
    proc.wait(timeout=5)
    
    lines = runtime.tail_log(tool, n=2)
    assert len(lines) == 2
    assert lines[0] == "line2"
    assert lines[1] == "line3"

def test_crash_detection():
    tool = "test_tool_crash"
    proc, log_path = runtime.launch_tool(tool, ["python3", "-c", "import sys; sys.exit(1)"])
    proc.wait(timeout=5)
    assert proc.returncode != 0

if __name__ == "__main__":
    print("Running test_write_read_state...")
    test_write_read_state()
    print("Running test_launch_creates_log...")
    test_launch_creates_log()
    print("Running test_tail_log...")
    test_tail_log()
    print("Running test_crash_detection...")
    test_crash_detection()
    print("All tests passed!")
