import ast
import os
import subprocess
import sys
import tempfile
import time

from backend.config import BLOCKED_IMPORTS, BLOCKED_NAMES, MAX_EXECUTION_TIME, MAX_OUTPUT_SIZE


def validate_code(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_IMPORTS:
                    raise ValueError(f"blocked import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in BLOCKED_IMPORTS:
                raise ValueError(f"blocked import: {node.module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_NAMES:
            raise ValueError(f"blocked builtin: {node.func.id}")


def run_code(code: str) -> dict:
    start = time.time()
    workdir = tempfile.mkdtemp(prefix="epochesque_")
    path = os.path.join(workdir, "run.py")
    try:
        validate_code(code)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PYTHONIOENCODING": "utf-8", "PATH": os.environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, path],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=MAX_EXECUTION_TIME,
            env=env,
        )
        latency_ms = int((time.time() - start) * 1000)
        stdout = result.stdout[:MAX_OUTPUT_SIZE]
        stderr = result.stderr[:MAX_OUTPUT_SIZE]
        if result.returncode == 0:
            return {"success": True, "input": {"code": code}, "output": {"stdout": stdout}, "latency_ms": latency_ms}
        error_type = "RuntimeError"
        if stderr:
            lines = [x for x in stderr.splitlines() if x.strip()]
            if lines and ":" in lines[-1]:
                error_type = lines[-1].split(":", 1)[0].split()[-1]
        return {"success": False, "input": {"code": code}, "output": {"stderr": stderr}, "error_type": error_type, "error_message": stderr.strip() or "unknown", "latency_ms": latency_ms}
    except subprocess.TimeoutExpired:
        return {"success": False, "input": {"code": code}, "output": {}, "error_type": "TimeoutError", "error_message": f"execution exceeded {MAX_EXECUTION_TIME}s", "latency_ms": MAX_EXECUTION_TIME * 1000}
    except (SyntaxError, ValueError) as exc:
        return {"success": False, "input": {"code": code}, "output": {}, "error_type": type(exc).__name__, "error_message": str(exc), "latency_ms": int((time.time() - start) * 1000)}
    finally:
        try:
            os.remove(path)
            os.rmdir(workdir)
        except OSError:
            pass
