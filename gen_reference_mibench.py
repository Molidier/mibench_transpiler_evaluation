#!/usr/bin/env python3
"""
gen_reference_mibench.py
--------------------------
Runs on Linux x86-64 (Ryzen).
Builds separate small/large binaries from small_srcs/large_srcs in metadata.

Fixes:
  - Uses small_srcs/large_srcs directly (not asm_files)
  - patch_and_run handles both ./binary and bare binary names
  - large binary named correctly to match run_large.sh
  - input file copying from repo data dirs

Usage:
    python mibench_transpiler_evaluation/gen_reference_mibench.py
    python mibench_transpiler_evaluation/gen_reference_mibench.py --root mibench_problems --repo mibench_repo --no-large
    python mibench_transpiler_evaluation/gen_reference_mibench.py --root mibench_problems --repo mibench_repo --benchmark automotive_basicmath
"""

import json
import re
import argparse
import subprocess
import tempfile
import shutil
from pathlib import Path
from collections import defaultdict

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kw):
        return x

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


# ---------------------------------------------------------------------------
# Assemble + link
# ---------------------------------------------------------------------------

def assemble_and_link(
    srcs:            list[str],
    asm_dir:         Path,
    binary_out:      Path,
    compiler:        str = "gcc",
    extra_ldflags:   list[str] = None,
) -> tuple[bool, str]:
    asm_files = []
    for src in srcs:
        stem = Path(src).stem
        asm  = asm_dir / f"{stem}.s"
        if not asm.exists():
            return False, f"Missing asm file: {asm.name} (was compile_mibench_asm.py run?)"
        asm_files.append(asm)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp  = Path(tmpdir)
        objs = []
        for asm in asm_files:
            obj = tmp / (asm.stem + ".o")
            result = subprocess.run(
                [compiler, "-c", "-o", str(obj), str(asm)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Assemble failed [{asm.name}]:\n{result.stderr.strip()}"
            objs.append(str(obj))

        result = subprocess.run(
            [compiler, "-o", str(binary_out)] + objs + ["-lm"] + (extra_ldflags or []),
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"Link failed:\n{result.stderr.strip()}"

    return True, ""


# ---------------------------------------------------------------------------
# Run script patching
# ---------------------------------------------------------------------------

def patch_and_run(
    binary:     Path,
    run_script: Path,
    cwd:        Path,
    timeout:    int = 60,
) -> tuple[int, str, str]:
    """
    Run benchmark via its run script.
    Handles both:
      ./binary_name arg1 arg2   (with ./ prefix)
      binary_name arg1 arg2     (bare name, no ./ prefix)
    Replaces the binary call with the full tmp path.
    """
    if not run_script.exists():
        return -1, "", f"Run script not found: {run_script.name}"

    binary_stem   = binary.name          # e.g. "basicmath_large"
    content       = run_script.read_text(errors="replace")
    patched_lines = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            patched_lines.append(line)
            continue

        # Match ./binary_name OR bare binary_name at start of a command token
        # Use count=1 so only the executable is replaced, not arguments
        line = re.sub(
            rf"(?<![/\w])(\./)?{re.escape(binary_stem)}\b",
            str(binary),
            line,
            count=1,
        )
        patched_lines.append(line)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write("\n".join(patched_lines))
        patched_path = Path(f.name)

    patched_path.chmod(0o755)
    try:
        result = subprocess.run(
            ["bash", str(patched_path)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout (>{timeout}s)"
    finally:
        patched_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Reference output saving
# ---------------------------------------------------------------------------

def save_reference(
    ref_dir:   Path,
    size:      str,
    exit_code: int,
    stdout:    str,
    stderr:    str,
):
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / f"{size}.exitcode").write_text(str(exit_code))
    (ref_dir / f"{size}.stdout").write_text(stdout)
    (ref_dir / f"{size}.stderr").write_text(stderr)


# ---------------------------------------------------------------------------
# Input file management
# ---------------------------------------------------------------------------

def ensure_input_files(
    problem_dir: Path,
    repo_root:   Path | None,
    subdir:      str | None,
    extra_input_paths: list[str] | None = None,
):
    """
    Copy input data files from repo into problem_dir/input/.
    Checks: bench_dir/data/, bench_dir/../data/, bench_dir/, bench_dir/../
    """
    if repo_root is None or subdir is None:
        return

    bench_dir = repo_root / subdir
    inp_out   = problem_dir / "input"
    inp_out.mkdir(exist_ok=True)

    data_exts = {".pgm", ".ppm", ".asc", ".dat", ".udp",
                 ".pcm", ".adpcm", ".bin", ".wav", ".txt"}

    search_dirs = [
        bench_dir / "data",
        bench_dir.parent / "data",
        bench_dir,
        bench_dir.parent,
    ]

    for data_dir in search_dirs:
        if not data_dir.exists() or not data_dir.is_dir():
            continue
        for f in data_dir.iterdir():
            if f.is_file() and f.suffix in data_exts:
                dest = inp_out / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)

    for rel_path in extra_input_paths or []:
        candidate = (bench_dir / rel_path).resolve()
        if candidate.exists() and candidate.is_file():
            dest = inp_out / candidate.name
            if not dest.exists():
                shutil.copy2(candidate, dest)


# ---------------------------------------------------------------------------
# Large binary name
# ---------------------------------------------------------------------------

def large_binary_name_for(binary_name: str) -> str:
    if "_small" in binary_name:
        return binary_name.replace("_small", "_large")
    return binary_name + "_large"


def exit_code_is_success(exit_code: int, success_exit_codes: list[int]) -> bool:
    return exit_code in set(success_exit_codes or [0])


# ---------------------------------------------------------------------------
# Per-benchmark processing
# ---------------------------------------------------------------------------

def process_benchmark(
    problem_dir: Path,
    run_large:   bool,
    repo_root:   Path | None = None,
) -> dict:
    meta_path = problem_dir / "metadata.json"
    if not meta_path.exists():
        return {
            "problem_dir": problem_dir.name,
            "benchmark":   problem_dir.name,
            "category":    "unknown",
            "status":      FAIL,
            "detail":      "metadata.json not found",
            "small":       None,
            "large":       None,
        }

    metadata = json.loads(meta_path.read_text())

    extra_ldflags = metadata.get("extra_ldflags", [])
    benchmark   = metadata.get("benchmark",   problem_dir.name)
    category    = metadata.get("category",    "unknown")
    binary_name = metadata.get("binary_name", benchmark)
    small_srcs  = metadata.get("small_srcs",  [])
    large_srcs  = metadata.get("large_srcs",  None)
    subdir      = metadata.get("subdir",      None)
    extra_input_paths = metadata.get("extra_input_paths", [])
    success_exit_codes = metadata.get("success_exit_codes", [0])

    result = {
        "problem_dir": problem_dir.name,
        "benchmark":   benchmark,
        "category":    category,
        "status":      None,
        "detail":      "",
        "small":       None,
        "large":       None,
    }

    asm_dir = problem_dir / "asm"
    ref_dir = problem_dir / "reference"

    if not asm_dir.exists() or not any(asm_dir.glob("*.s")):
        result.update({
            "status": FAIL,
            "detail": "asm/ empty — run compile_mibench_asm.py first"
        })
        return result

    if repo_root:
        ensure_input_files(problem_dir, repo_root, subdir, extra_input_paths)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # ── small binary ────────────────────────────────────────────────────
        small_binary = tmp / binary_name
        ok, err = assemble_and_link(small_srcs, asm_dir, small_binary,
                             extra_ldflags=extra_ldflags)
        if not ok:
            result.update({"status": FAIL, "detail": err})
            return result

        exit_code, stdout, stderr = patch_and_run(
            small_binary,
            problem_dir / "run_small.sh",
            cwd=problem_dir,
            timeout=60,
        )
        save_reference(ref_dir, "small", exit_code, stdout, stderr)
        result["small"] = exit_code

        if not exit_code_is_success(exit_code, success_exit_codes):
            result.update({
                "status": FAIL,
                "detail": f"run_small exit {exit_code}: {(stdout + stderr)[:200]}"
            })
            return result

        # ── large binary ────────────────────────────────────────────────────
        if not run_large:
            result["large"] = SKIP

        elif large_srcs is None:
            # Same program, different CLI args — reuse small binary
            exit_code, stdout, stderr = patch_and_run(
                small_binary,
                problem_dir / "run_large.sh",
                cwd=problem_dir,
                timeout=120,
            )
            save_reference(ref_dir, "large", exit_code, stdout, stderr)
            result["large"] = exit_code
            if not exit_code_is_success(exit_code, success_exit_codes):
                result.update({
                    "status": FAIL,
                    "detail": f"run_large exit {exit_code}: {(stdout + stderr)[:200]}"
                })
                return result

        elif large_srcs == []:
            result["large"] = SKIP

        else:
            # Different program for large — build separately with derived name
            lbin_name    = large_binary_name_for(binary_name)
            large_binary = tmp / lbin_name
            ok, err = assemble_and_link(
                large_srcs,
                asm_dir,
                large_binary,
                extra_ldflags=extra_ldflags,
            )
            if not ok:
                result.update({"status": FAIL, "detail": f"large build: {err}"})
                return result

            exit_code, stdout, stderr = patch_and_run(
                large_binary,
                problem_dir / "run_large.sh",
                cwd=problem_dir,
                timeout=120,
            )
            save_reference(ref_dir, "large", exit_code, stdout, stderr)
            result["large"] = exit_code
            if not exit_code_is_success(exit_code, success_exit_codes):
                result.update({
                    "status": FAIL,
                    "detail": f"run_large exit {exit_code}: {(stdout + stderr)[:200]}"
                })
                return result

    result.update({
        "status": PASS,
        "detail": f"Reference saved -> {ref_dir.relative_to(problem_dir.parent)}"
    })
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate reference outputs from asm/*.s (Linux x86)."
    )
    parser.add_argument("--root",         default=str(PROJECT_ROOT / "mibench_problems"))
    parser.add_argument("--repo",         default=str(PROJECT_ROOT / "mibench_repo"))
    parser.add_argument("--out",          default=str(PROJECT_ROOT / "results_reference.json"))
    parser.add_argument("--no-large",     action="store_true")
    parser.add_argument("--benchmark",    default=None)
    parser.add_argument("--stop-on-fail", action="store_true")
    args = parser.parse_args()

    root      = Path(args.root)
    repo_root = Path(args.repo) if Path(args.repo).exists() else None

    if not root.exists():
        print(f"ERROR: '{root}' not found.")
        return
    if repo_root is None:
        print(f"WARNING: repo dir '{args.repo}' not found — input file lookup disabled.")

    all_dirs = sorted(
        [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda p: p.name,
    )
    if args.benchmark:
        all_dirs = [d for d in all_dirs if d.name == args.benchmark]
        if not all_dirs:
            print(f"ERROR: '{args.benchmark}' not found.")
            return

    if not all_dirs:
        print(f"ERROR: No benchmark dirs found in '{root}/'")
        return

    print(f"Generating reference outputs for {len(all_dirs)} benchmarks\n")

    results  = []
    n_pass = n_fail = 0

    for problem_dir in tqdm(all_dirs, desc="Generating"):
        r = process_benchmark(
            problem_dir,
            run_large=not args.no_large,
            repo_root=repo_root,
        )
        results.append(r)

        icon = "✓" if r["status"] == PASS else "✗"
        tqdm.write(
            f"  [{icon}] {r['problem_dir']:30s}  "
            f"small={r['small']}  large={r['large']}"
            + (f"\n       {r['detail']}" if r["status"] != PASS
               else f"  — {r['detail']}")
        )

        if r["status"] == PASS: n_pass += 1
        else:                   n_fail += 1

        if args.stop_on_fail and r["status"] != PASS:
            print("\nStopped at first failure.")
            break

    total = len(results)
    print(f"\n{'='*55}")
    print(f"Results : {n_pass}/{total} passed  |  {n_fail} failed")
    if total > 0:
        print(f"Pass rate: {n_pass/total*100:.1f}%")
    print(f"{'='*55}")

    by_cat = defaultdict(lambda: {"pass": 0, "fail": 0})
    for r in results:
        by_cat[r.get("category", "unknown")][
            "pass" if r["status"] == PASS else "fail"
        ] += 1

    print("\nBy category:")
    for cat, counts in sorted(by_cat.items()):
        t = counts["pass"] + counts["fail"]
        print(f"  {cat:15s}  {counts['pass']}/{t}")

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nReference outputs saved inside each problem dir under reference/")
    print(f"Results saved to {args.out}")
    print(
        "\nNext: fill pred_asm/*.s with LLM translations, then run "
        "python mibench_transpiler_evaluation/eval_pred_mibench.py"
    )


if __name__ == "__main__":
    main()
