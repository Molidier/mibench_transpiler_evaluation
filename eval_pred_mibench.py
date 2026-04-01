#!/usr/bin/env python3
"""
eval_pred_mibench.py
---------------------
Runs on Mac (Apple Silicon).
Assembles pred_asm/*.s per-file -> .o -> links -> runs -> diffs against reference.

Requires:
  arm64  : Xcode Command Line Tools (`xcode-select --install`)
  riscv64: either
           - `brew tap riscv-software-src/riscv && brew install riscv-tools`
           - or a RISC-V Linux cross-toolchain plus `qemu-riscv64`

Usage:
    python mibench_transpiler_evaluation/eval_pred_mibench.py --arch arm64
    python mibench_transpiler_evaluation/eval_pred_mibench.py --arch riscv64
    python mibench_transpiler_evaluation/eval_pred_mibench.py --root mibench_problems --arch arm64 --diff-tolerance 2
"""

import json
import platform
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

ARCH_CONFIGS = {
    "arm64": {
        "description":  "AArch64 — native Apple Silicon",
        "backends": [
            {
                "name":                 "native-clang",
                "assembler_candidates": ["clang"],
                "asm_flags":            ["-arch", "arm64", "-c"],
                "linker_candidates":    ["clang"],
                "link_flags":           ["-arch", "arm64"],
                "runner_candidates":    [],
                "runner_args":          [],
                "install_hint":         "xcode-select --install",
            },
        ],
    },
    "riscv64": {
        "description":  "RISC-V 64-bit via simulator",
        "backends": [
            {
                "name":                 "spike-pk",
                "assembler_candidates": ["riscv64-elf-gcc", "riscv64-unknown-elf-gcc"],
                "asm_flags":            ["-c", "-march=rv64gc", "-mabi=lp64d"],
                "linker_candidates":    ["riscv64-elf-gcc", "riscv64-unknown-elf-gcc"],
                "link_flags":           ["-march=rv64gc", "-mabi=lp64d", "-static"],
                "runner_candidates":    ["spike"],
                "runner_args":          ["pk"],
                "install_hint": (
                    "brew tap riscv-software-src/riscv && brew install riscv-tools"
                ),
            },
            {
                "name":                 "qemu-linux-user",
                "assembler_candidates": ["riscv64-linux-gnu-gcc", "riscv64-unknown-linux-gnu-gcc"],
                "asm_flags":            ["-c", "-march=rv64gc", "-mabi=lp64d"],
                "linker_candidates":    ["riscv64-linux-gnu-gcc", "riscv64-unknown-linux-gnu-gcc"],
                "link_flags":           ["-march=rv64gc", "-mabi=lp64d", "-static"],
                "runner_candidates":    ["qemu-riscv64"],
                "runner_args":          [],
                "install_hint":         (
                    "install a RISC-V Linux cross-compiler and make sure qemu-riscv64 is on PATH"
                ),
            },
        ],
    },
}


def find_first_executable(candidates: list[str]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def check_host_support(arch: str) -> tuple[bool, str]:
    system  = platform.system()
    machine = platform.machine().lower()

    if arch == "arm64":
        if system != "Darwin":
            return False, "arm64 evaluation is intended to run on macOS (Darwin)."
        if machine not in {"arm64", "aarch64"}:
            return False, (
                f"arm64 evaluation on macOS requires Apple Silicon; found host machine '{platform.machine()}'."
            )

    return True, ""


def resolve_backend(arch: str) -> tuple[dict | None, str]:
    cfg      = ARCH_CONFIGS[arch]
    failures = []

    for backend in cfg["backends"]:
        assembler = find_first_executable(backend["assembler_candidates"])
        linker    = find_first_executable(backend["linker_candidates"])
        runner    = (
            find_first_executable(backend["runner_candidates"])
            if backend["runner_candidates"] else None
        )

        missing = []
        if assembler is None:
            missing.append(f"assembler {backend['assembler_candidates']}")
        if linker is None:
            missing.append(f"linker {backend['linker_candidates']}")
        if backend["runner_candidates"] and runner is None:
            missing.append(f"runner {backend['runner_candidates']}")

        if missing:
            failures.append(
                f"{backend['name']}: missing {', '.join(missing)}; install with {backend['install_hint']}"
            )
            continue

        resolved = dict(backend)
        resolved["assembler"] = assembler
        resolved["linker"]    = linker
        resolved["runner"]    = runner
        return resolved, ""

    return None, "\n  ".join(failures)


def assemble_and_link(pred_files: list[Path], binary_out: Path,
                      backend: dict) -> tuple[bool, str]:
    """Assemble each pred_asm/*.s -> .o individually, then link."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp  = Path(tmpdir)
        objs = []

        for asm in pred_files:
            obj = tmp / (asm.stem + ".o")
            result = subprocess.run(
                [backend["assembler"]] + backend["asm_flags"] + ["-o", str(obj), str(asm)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Assemble failed [{asm.name}]:\n{result.stderr.strip()}"
            objs.append(str(obj))

        result = subprocess.run(
            [backend["linker"]] + backend["link_flags"] + ["-o", str(binary_out)] + objs + ["-lm"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"Link failed:\n{result.stderr.strip()}"

    return True, ""


def run_with_script(binary: Path, run_script: Path, backend: dict,
                    cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    if not run_script.exists():
        return -1, "", f"Run script not found: {run_script.name}"

    runner_prefix = backend.get("runner_args", [])
    if backend.get("runner"):
        runner_prefix = [backend["runner"]] + runner_prefix

    binary_token = " ".join(runner_prefix + [str(binary)]) if runner_prefix else str(binary)
    content       = run_script.read_text(errors="replace")
    patched_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            patched_lines.append(line)
            continue
        line = re.sub(
            rf"(?<![/\w])(\./)?{re.escape(binary.name)}\b",
            binary_token,
            line,
            count=1,
        )
        patched_lines.append(line)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write("\n".join(patched_lines))
        patched_path = Path(f.name)

    patched_path.chmod(0o755)
    try:
        result = subprocess.run(
            ["bash", str(patched_path)],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout (>{timeout}s)"
    finally:
        patched_path.unlink(missing_ok=True)


def load_reference(ref_dir: Path, size: str) -> tuple[int | None, str | None]:
    ec_path  = ref_dir / f"{size}.exitcode"
    out_path = ref_dir / f"{size}.stdout"
    if not ec_path.exists() or not out_path.exists():
        return None, None
    return int(ec_path.read_text().strip()), out_path.read_text()


def diff_output(actual: str, expected: str, tolerance: int) -> tuple[bool, str]:
    a_lines = actual.strip().splitlines()
    e_lines = expected.strip().splitlines()
    if a_lines == e_lines:
        return True, "exact match"

    diffs = []
    for i, (a, e) in enumerate(zip(a_lines, e_lines)):
        if a != e:
            diffs.append(f"  line {i+1}: got      '{a}'")
            diffs.append(f"           expected '{e}'")

    len_diff    = abs(len(a_lines) - len(e_lines))
    total_diffs = len(diffs) // 2 + len_diff

    if total_diffs <= tolerance:
        return True, f"within tolerance ({total_diffs} differing lines)"

    summary = "\n".join(diffs[:10])
    if len(diffs) > 10:
        summary += f"\n  ... and {len(diffs)//2 - 5} more differing lines"
    if len_diff:
        summary += f"\n  line count: got {len(a_lines)}, expected {len(e_lines)}"
    return False, summary


def evaluate_size(binary: Path, run_script: Path, ref_dir: Path,
                  size: str, backend: dict, cwd: Path,
                  tolerance: int, timeout: int) -> dict:
    ref_exit, ref_stdout = load_reference(ref_dir, size)
    if ref_exit is None:
        return {"status": SKIP, "detail": f"No reference for {size}"}

    exit_code, stdout, stderr = run_with_script(binary, run_script, backend, cwd, timeout)

    if exit_code == -1:
        return {"status": FAIL, "detail": stderr[:200]}

    exit_ok       = (exit_code == ref_exit)
    diff_ok, detail = diff_output(stdout, ref_stdout, tolerance)

    if exit_ok and diff_ok:
        return {"status": PASS, "detail": detail}

    parts = []
    if not exit_ok:
        parts.append(f"exit code: got {exit_code}, expected {ref_exit}")
    if not diff_ok:
        parts.append(f"output diff:\n{detail}")
    return {"status": FAIL, "detail": "\n".join(parts)}


def large_binary_name_for(binary_name: str) -> str:
    if "_small" in binary_name:
        return binary_name.replace("_small", "_large")
    return binary_name + "_large"


def collect_pred_files(pred_asm_dir: Path, asm_names: list[str] | None,
                       fallback_all: bool = False) -> tuple[list[Path], list[str]]:
    if asm_names:
        pred_files = [pred_asm_dir / name for name in asm_names if (pred_asm_dir / name).exists()]
        missing    = [name for name in asm_names if not (pred_asm_dir / name).exists()]
        return pred_files, missing

    if fallback_all:
        return sorted(pred_asm_dir.glob("*.s")), []

    return [], []


def find_placeholders(pred_files: list[Path]) -> list[str]:
    return [
        f.name for f in pred_files
        if "Translate" in f.read_text(errors="replace")[:80]
    ]


def process_benchmark(problem_dir: Path, arch: str,
                       run_large: bool, tolerance: int, backend: dict) -> dict:
    meta_path = problem_dir / "metadata.json"
    metadata  = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    benchmark       = metadata.get("benchmark", problem_dir.name)
    category        = metadata.get("category",  "unknown")
    binary_name     = metadata.get("binary_name", benchmark)
    small_asm_names = metadata.get("asm_files", [])
    large_asm_names = metadata.get("asm_files_large", None)

    result = {
        "problem_dir": problem_dir.name,
        "benchmark":   benchmark,
        "category":    category,
        "arch":        arch,
        "status":      None,
        "assemble":    None,
        "small":       None,
        "large":       None,
        "detail":      "",
    }

    pred_asm_dir = problem_dir / "pred_asm"
    ref_dir      = problem_dir / "reference"

    # Sanity checks
    if not pred_asm_dir.exists():
        result.update({"status": FAIL, "detail": "pred_asm/ not found"})
        return result
    if not ref_dir.exists():
        result.update({"status": FAIL,
                        "detail": "reference/ not found — run gen_reference_mibench.py on Linux first"})
        return result

    small_pred_files, missing = collect_pred_files(
        pred_asm_dir,
        small_asm_names,
        fallback_all=True,
    )
    if missing:
        result.update({"status": FAIL,
                       "detail": f"Missing pred_asm files for small build: {missing}"})
        return result

    if not small_pred_files:
        result.update({"status": FAIL, "detail": "No .s files in pred_asm/"})
        return result

    placeholders = find_placeholders(small_pred_files)
    if placeholders:
        result.update({"status": FAIL,
                        "detail": f"Placeholder files not yet translated: {placeholders}"})
        return result

    with tempfile.TemporaryDirectory() as tmpdir:
        small_binary = Path(tmpdir) / binary_name

        ok, err = assemble_and_link(small_pred_files, small_binary, backend)
        result["assemble"] = PASS if ok else FAIL
        if not ok:
            result.update({"status": FAIL, "detail": err})
            return result

        # Evaluate small
        small = evaluate_size(
            small_binary, problem_dir / "run_small.sh",
            ref_dir, "small", backend, problem_dir, tolerance, timeout=60
        )
        result["small"] = small["status"]
        if small["status"] == FAIL:
            result.update({"status": FAIL, "detail": f"small: {small['detail']}"})
            return result

        # Evaluate large
        if run_large and (problem_dir / "run_large.sh").exists():
            large_binary = small_binary

            if large_asm_names not in (None, []):
                large_pred_files, missing = collect_pred_files(
                    pred_asm_dir,
                    large_asm_names,
                )
                if missing:
                    result.update({
                        "status": FAIL,
                        "detail": f"Missing pred_asm files for large build: {missing}"
                    })
                    return result

                placeholders = find_placeholders(large_pred_files)
                if placeholders:
                    result.update({
                        "status": FAIL,
                        "detail": f"Placeholder files not yet translated for large build: {placeholders}"
                    })
                    return result

                large_binary = Path(tmpdir) / large_binary_name_for(binary_name)
                ok, err = assemble_and_link(large_pred_files, large_binary, backend)
                result["assemble"] = PASS if ok else FAIL
                if not ok:
                    result.update({"status": FAIL, "detail": f"large build: {err}"})
                    return result

            large = evaluate_size(
                large_binary, problem_dir / "run_large.sh",
                ref_dir, "large", backend, problem_dir, tolerance, timeout=120
            )
            result["large"] = large["status"]
            if large["status"] == FAIL:
                result.update({"status": FAIL, "detail": f"large: {large['detail']}"})
                return result
        else:
            result["large"] = SKIP

    result.update({"status": PASS, "detail": "All checks passed"})
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate pred_asm/*.s against reference outputs (run on Mac)."
    )
    parser.add_argument("--root",           default=str(PROJECT_ROOT / "mibench_problems"))
    parser.add_argument("--arch",           choices=list(ARCH_CONFIGS.keys()), required=True)
    parser.add_argument("--out",            default=None)
    parser.add_argument("--no-large",       action="store_true")
    parser.add_argument("--diff-tolerance", type=int, default=0)
    parser.add_argument("--benchmark",      default=None)
    parser.add_argument("--stop-on-fail",   action="store_true")
    args = parser.parse_args()

    root     = Path(args.root)
    out_path = Path(args.out or (PROJECT_ROOT / f"results_{args.arch}.json"))
    cfg      = ARCH_CONFIGS[args.arch]

    if not root.exists():
        print(f"ERROR: '{root}' not found.")
        return

    ok, err = check_host_support(args.arch)
    if not ok:
        print(f"ERROR: {err}")
        return

    backend, err = resolve_backend(args.arch)
    if backend is None:
        print(f"ERROR: no usable {args.arch} toolchain found.")
        print(f"  {err}")
        return

    print(f"Arch      : {args.arch} — {cfg['description']}")
    print(f"Backend   : {backend['name']}")
    print(f"Tolerance : {args.diff_tolerance} differing lines")
    print(f"Root      : {root}/\n")

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

    print(f"Found {len(all_dirs)} benchmarks\n")

    results  = []
    n_pass = n_fail = 0

    for problem_dir in tqdm(all_dirs, desc=f"Evaluating [{args.arch}]"):
        r = process_benchmark(
            problem_dir,
            args.arch,
            not args.no_large,
            args.diff_tolerance,
            backend,
        )
        results.append(r)

        status = r["status"]
        icon   = "✓" if status == PASS else "✗"
        tqdm.write(
            f"  [{icon}] {r['problem_dir']:30s}  "
            f"asm={r['assemble'] or '-':4s}  "
            f"small={r['small'] or '-':4s}  "
            f"large={r['large'] or '-':4s}"
            + (f"\n       {r['detail'][:120]}" if status != PASS else "")
        )

        if status == PASS: n_pass += 1
        else:              n_fail += 1

        if args.stop_on_fail and status != PASS:
            print("\nStopped at first failure.")
            break

    total = len(results)
    print(f"\n{'='*60}")
    print(f"Arch      : {args.arch}")
    print(f"Results   : {n_pass}/{total} passed  |  {n_fail} failed")
    if total > 0:
        print(f"Pass rate : {n_pass/total*100:.1f}%")
    print(f"{'='*60}")

    by_cat = defaultdict(lambda: {"pass": 0, "fail": 0})
    for r in results:
        by_cat[r.get("category","unknown")]["pass" if r["status"] == PASS else "fail"] += 1

    print("\nBy category:")
    for cat, counts in sorted(by_cat.items()):
        t = counts["pass"] + counts["fail"]
        print(f"  {cat:15s}  {counts['pass']}/{t}")

    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
