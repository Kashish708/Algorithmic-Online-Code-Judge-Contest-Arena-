import os
import subprocess
import time
import uuid
import math
import psutil

TEMP_DIR = os.path.join(os.getcwd(), "temp_runs")
os.makedirs(TEMP_DIR, exist_ok=True)

def run_single_process(command, stdin_data, time_limit, mem_limit_mb):
    """Executes a command while measuring peak memory (MB) and wall time (ms)."""
    start_time = time.perf_counter()
    peak_memory_bytes = 0

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except FileNotFoundError as e:
        return {
            "status": "Runtime Error",
            "stdout": "",
            "stderr": f"Compiler/Runtime binary not found: {str(e)}",
            "time_ms": 0.0,
            "memory_mb": 0.0
        }

    ps_proc = None
    try:
        ps_proc = psutil.Process(proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    try:
        try:
            if stdin_data:
                proc.stdin.write(stdin_data)
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            # Safe ignore: Process died before we could feed it data.
            pass

        while proc.poll() is None:
            elapsed = time.perf_counter() - start_time
            if elapsed > time_limit:
                proc.kill()
                return {
                    "status": "Time Limit Exceeded",
                    "stdout": "",
                    "stderr": f"Exceeded time limit of {time_limit}s",
                    "time_ms": round(elapsed * 1000, 2),
                    "memory_mb": round(peak_memory_bytes / (1024 * 1024), 2)
                }

            if ps_proc:
                try:
                    mem = ps_proc.memory_info().rss
                    if mem > peak_memory_bytes:
                        peak_memory_bytes = mem
                    if peak_memory_bytes > (mem_limit_mb * 1024 * 1024):
                        proc.kill()
                        return {
                            "status": "Memory Limit Exceeded",
                            "stdout": "",
                            "stderr": f"Exceeded memory limit of {mem_limit_mb}MB",
                            "time_ms": round(elapsed * 1000, 2),
                            "memory_mb": round(peak_memory_bytes / (1024 * 1024), 2)
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            time.sleep(0.01)

        stdout_data, stderr_data = proc.communicate()
        total_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
        total_mem_mb = round(peak_memory_bytes / (1024 * 1024), 2)

        if proc.returncode != 0:
            return {
                "status": "Runtime Error",
                "stdout": stdout_data.strip(),
                "stderr": stderr_data.strip(),
                "time_ms": total_time_ms,
                "memory_mb": total_mem_mb
            }

        return {
            "status": "Success",
            "stdout": stdout_data.strip(),
            "stderr": "",
            "time_ms": total_time_ms,
            "memory_mb": total_mem_mb
        }

    except Exception as e:
        proc.kill()
        return {
            "status": "Runtime Error",
            "stdout": "",
            "stderr": str(e),
            "time_ms": 0.0,
            "memory_mb": 0.0
        }

def evaluate_checker(actual: str, expected: str, checker_type: str) -> bool:
    """Supports strict exact matching or tolerant floating-point checks."""
    actual_clean = actual.strip().replace("\r\n", "\n")
    expected_clean = expected.strip().replace("\r\n", "\n")

    if checker_type == "floats":
        act_tokens = actual_clean.split()
        exp_tokens = expected_clean.split()
        if len(act_tokens) != len(exp_tokens):
            return False
        for a, e in zip(act_tokens, exp_tokens):
            try:
                if not math.isclose(float(a), float(e), rel_tol=1e-5, abs_tol=1e-5):
                    return False
            except ValueError:
                if a != e:
                    return False
        return True

    act_lines = [l.strip() for l in actual_clean.split("\n")]
    exp_lines = [l.strip() for l in expected_clean.split("\n")]
    return act_lines == exp_lines

def execute_all_test_cases(code: str, language: str, test_cases: list, time_limit: float, mem_limit_mb: float, checker_type: str = "exact", progress_cb=None):
    """Compiles and executes code against multiple test cases, supporting real-time WebSocket progress callbacks."""
    run_id = str(uuid.uuid4())[:8]
    lang = language.lower()
    cleanup_files = []

    if lang == "python":
        file_path = os.path.join(TEMP_DIR, f"{run_id}.py")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
        run_command = ["python", file_path]
        cleanup_files.append(file_path)

    elif lang == "cpp":
        src = os.path.join(TEMP_DIR, f"{run_id}.cpp")
        exe_name = f"{run_id}.exe" if os.name == "nt" else run_id
        exe = os.path.join(TEMP_DIR, exe_name)
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)
        if progress_cb: progress_cb("Compiling...")
        comp = subprocess.run(["g++", "-O2", src, "-o", exe], capture_output=True, text=True, timeout=15)
        cleanup_files.extend([src, exe])
        if comp.returncode != 0:
            return {"status": "Compile Error", "error": comp.stderr, "score": 0, "total_score": sum(tc.weight for tc in test_cases), "time_ms": 0, "memory_mb": 0}
        run_command = [exe]

    elif lang == "java":
        src = os.path.join(TEMP_DIR, "Main.java")
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)
        if progress_cb: progress_cb("Compiling...")
        comp = subprocess.run(["javac", src], capture_output=True, text=True, timeout=15)
        cleanup_files.extend([src, os.path.join(TEMP_DIR, "Main.class")])
        if comp.returncode != 0:
            return {"status": "Compile Error", "error": comp.stderr, "score": 0, "total_score": sum(tc.weight for tc in test_cases), "time_ms": 0, "memory_mb": 0}
        run_command = ["java", "-cp", TEMP_DIR, "Main"]

    elif lang in ["javascript", "js"]:
        file_path = os.path.join(TEMP_DIR, f"{run_id}.js")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
        run_command = ["node", file_path]
        cleanup_files.append(file_path)

    elif lang == "rust":
        src = os.path.join(TEMP_DIR, f"{run_id}.rs")
        exe_name = f"{run_id}.exe" if os.name == "nt" else run_id
        exe = os.path.join(TEMP_DIR, exe_name)
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)
        if progress_cb: progress_cb("Compiling...")
        comp = subprocess.run(["rustc", "-O", src, "-o", exe], capture_output=True, text=True, timeout=20)
        cleanup_files.extend([src, exe, os.path.join(TEMP_DIR, f"{run_id}.pdb")])
        if comp.returncode != 0:
            return {"status": "Compile Error", "error": comp.stderr, "score": 0, "total_score": sum(tc.weight for tc in test_cases), "time_ms": 0, "memory_mb": 0}
        run_command = [exe]

    else:
        return {"status": "Unsupported Language", "error": f"Language '{language}' is not supported.", "score": 0, "total_score": 0, "time_ms": 0, "memory_mb": 0}

    total_weight = sum(tc.weight for tc in test_cases)
    earned_score = 0
    max_time_ms = 0.0
    max_mem_mb = 0.0
    overall_status = "Accepted"
    last_error = ""

    try:
        for i, tc in enumerate(test_cases):
            if progress_cb: 
                progress_cb(f"Running Case {i+1} / {len(test_cases)}...")
            
            res = run_single_process(run_command, tc.input_data, time_limit, mem_limit_mb)
            max_time_ms = max(max_time_ms, res["time_ms"])
            max_mem_mb = max(max_mem_mb, res["memory_mb"])

            if res["status"] != "Success":
                overall_status = res["status"]
                last_error = res["stderr"]
                break

            if evaluate_checker(res["stdout"], tc.expected_output, checker_type):
                earned_score += tc.weight
            else:
                overall_status = "Wrong Answer"
                break
    finally:
        for f in cleanup_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except PermissionError:
                    pass

    if overall_status != "Accepted" and earned_score > 0:
        final_verdict = "Partial"
    else:
        final_verdict = overall_status

    return {
        "status": final_verdict,
        "score": earned_score,
        "total_score": total_weight,
        "time_ms": max_time_ms,
        "memory_mb": max_mem_mb,
        "error": last_error
    }