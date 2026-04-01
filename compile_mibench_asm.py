#!/usr/bin/env python3
"""
compile_mibench_asm.py
-----------------------
Compiles each .c in src/ to its own per-file .s in asm/.
Uses small_srcs + large_srcs from metadata.
Supports extra_cflags per benchmark (e.g. -std=gnu89 for rijndael,
-I/usr/include/tirpc for patricia).

Usage:
    python mibench_transpiler_evaluation/compile_mibench_asm.py --mode linux-x64
    python mibench_transpiler_evaluation/compile_mibench_asm.py --root mibench_problems --mode linux-x64 --opt O0
"""

import json
import argparse
import subprocess
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

MODES = {
    "mac-x86": {
        "compiler":    "clang",
        "flags":       ["-S", "-arch", "x86_64",
                        "-masm=intel",
                        "-target", "x86_64-apple-macosx10.15.0"],
        "description": "macOS x86-64 via clang, Intel syntax",
    },
    "linux-x64": {
        "compiler":    "gcc",
        "flags":       ["-S", "-march=x86-64", "-masm=att"],
        "description": "Linux x86-64 via gcc, AT&T syntax (AMD64/Ryzen)",
    },
}


def compile_one(
    src:          Path,
    asm_out:      Path,
    mode:         dict,
    include_dirs: list[Path],
    opt:          str,
    extra_cflags: list[str] = None,
) -> tuple[bool, str]:
    inc_flags = [f"-I{d}" for d in include_dirs]
    cmd = (
        [mode["compiler"]]
        + [f"-{opt}"]
        + mode["flags"]
        + inc_flags
        + (extra_cflags or [])
        + ["-o", str(asm_out), str(src)]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


def process_benchmark(problem_dir: Path, mode_name: str, opt: str) -> dict:
    mode      = MODES[mode_name]
    meta_path = problem_dir / "metadata.json"
    metadata  = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    benchmark    = metadata.get("benchmark",    problem_dir.name)
    category     = metadata.get("category",     "unknown")
    entry_src    = metadata.get("entry_src")
    small_srcs   = metadata.get("small_srcs",   [])
    large_srcs   = metadata.get("large_srcs",   None) or []
    extra_cflags = metadata.get("extra_cflags", [])

    # Union of all files needed across small and large builds
    all_needed = sorted(set(small_srcs + large_srcs))

    result = {
        "problem_dir": problem_dir.name,
        "benchmark":   benchmark,
        "category":    category,
        "status":      None,
        "detail":      "",
        "compiled":    [],
        "failed":      [],
    }

    src_dir = problem_dir / "src"
    if not src_dir.exists():
        result.update({"status": FAIL, "detail": "No src/ directory found"})
        return result

    if not all_needed:
        result.update({"status": FAIL, "detail": "No srcs in metadata"})
        return result

    sources = []
    missing = []
    for fname in all_needed:
        fpath = src_dir / fname
        if fpath.exists():
            sources.append(fpath)
        else:
            missing.append(fname)

    if missing:
        result.update({
            "status": FAIL,
            "detail": f"Source files not found in src/: {missing}"
        })
        return result

    include_dirs = [src_dir]
    asm_dir      = problem_dir / "asm"
    pred_asm_dir = problem_dir / "pred_asm"
    asm_dir.mkdir(exist_ok=True)
    pred_asm_dir.mkdir(exist_ok=True)

    entry_asm = None
    all_ok    = True

    for src in sources:
        asm_out = asm_dir / (src.stem + ".s")
        ok, err = compile_one(src, asm_out, mode, include_dirs, opt, extra_cflags)

        if ok:
            result["compiled"].append(src.name)
            if entry_src and src.name == entry_src:
                entry_asm = asm_out
            elif entry_asm is None:
                entry_asm = asm_out

            pred_placeholder = pred_asm_dir / (src.stem + ".s")
            if not pred_placeholder.exists():
                pred_placeholder.write_text(
                    f"# Translate asm/{src.stem}.s from x86-64 to ARM64/RISC-V\n"
                    f"# and place result here\n"
                )
        else:
            result["failed"].append({"src": src.name, "error": err})
            all_ok = False

    if not result["compiled"]:
        result.update({"status": FAIL, "detail": "All source files failed to compile"})
        return result

    # Update metadata with correct asm_files (small only) and asm_files_large
    metadata["asm_files"] = [
        f"{Path(s).stem}.s" for s in small_srcs
    ]
    metadata["asm_files_large"] = (
        [f"{Path(s).stem}.s" for s in large_srcs]
        if large_srcs else None
    )
    metadata["entry_asm"] = entry_asm.name if entry_asm else None
    metadata["opt_level"] = opt
    metadata["mode"]      = mode_name
    meta_path.write_text(json.dumps(metadata, indent=2))

    if all_ok:
        result.update({
            "status": PASS,
            "detail": (
                f"Compiled {len(result['compiled'])} files -> asm/  |  "
                f"pred_asm/ placeholders created"
            ),
        })
    else:
        result.update({
            "status": FAIL,
            "detail": (
                f"Partial: {len(result['compiled'])}/{len(sources)} OK  |  "
                f"Failed: {[f['src'] for f in result['failed']]}"
            ),
        })

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Compile MiBench src/*.c -> per-file asm/*.s"
    )
    parser.add_argument("--root",         default=str(PROJECT_ROOT / "mibench_problems"))
    parser.add_argument("--mode",         choices=list(MODES.keys()), required=True)
    parser.add_argument("--opt",          choices=["O0","O1","O2","O3","Os"],
                        default="O2")
    parser.add_argument("--out",          default=str(PROJECT_ROOT / "results_mibench_asm.json"))
    parser.add_argument("--benchmark",    default=None)
    parser.add_argument("--stop-on-fail", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: '{root}' not found.")
        return

    mode     = MODES[args.mode]
    compiler = mode["compiler"]
    check    = subprocess.run(["which", compiler], capture_output=True, text=True)
    if check.returncode != 0:
        print(f"ERROR: compiler '{compiler}' not found.")
        print(f"  Install: "
              f"{'xcode-select --install' if args.mode == 'mac-x86' else 'sudo apt install gcc'}")
        return

    print(f"Mode : {args.mode} — {mode['description']}")
    print(f"Opt  : -{args.opt}")
    print(f"Root : {root}/\n")

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

    for problem_dir in tqdm(all_dirs, desc="Compiling"):
        r = process_benchmark(problem_dir, args.mode, args.opt)
        results.append(r)

        icon = "✓" if r["status"] == PASS else "✗"
        tqdm.write(
            f"  [{icon}] {r['problem_dir']:30s}  {r['status']}  — {r['detail']}"
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
        by_cat[r["category"]]["pass" if r["status"] == PASS else "fail"] += 1

    print("\nBy category:")
    for cat, counts in sorted(by_cat.items()):
        t = counts["pass"] + counts["fail"]
        print(f"  {cat:15s}  {counts['pass']}/{t}")

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nFull results saved to {args.out}")
    print(f"\nDirectory structure per benchmark:")
    print(f"  <benchmark>/")
    print(f"  ├── asm/          <- x86 .s files (LLM INPUT)")
    print(f"  └── pred_asm/     <- ARM/RISC-V .s files (LLM OUTPUT)")


if __name__ == "__main__":
    main()
