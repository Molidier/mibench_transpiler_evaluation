#!/usr/bin/env python3
"""
prepare_mibench.py
-------------------
Clones/reuses MiBench and organises each benchmark into problem dirs.

Usage:
    python mibench_transpiler_evaluation/prepare_mibench.py
    python mibench_transpiler_evaluation/prepare_mibench.py --clone-dir mibench_repo --out mibench_problems
    python mibench_transpiler_evaluation/prepare_mibench.py --clone-dir mibench_repo --categories automotive security
"""

import os
import re
import json
import shutil
import argparse
import subprocess
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kw):
        return x

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# Per-benchmark configuration
# ---------------------------------------------------------------------------

BENCH_CONFIG = {
    "automotive_basicmath": {
        "subdir":       "automotive/basicmath",
        "binary_name":  "basicmath_small",
        "small_srcs":   ["basicmath_small.c", "cubic.c", "isqrt.c", "rad2deg.c"],
        "large_srcs":   ["basicmath_large.c", "cubic.c", "isqrt.c", "rad2deg.c"],
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "automotive_bitcount": {
        "subdir":       "automotive/bitcount",
        "binary_name":  "bitcnts",
        "small_srcs":   ["bitcnts.c", "bitarray.c", "bitcnt_1.c", "bitcnt_2.c",
                         "bitcnt_3.c", "bitcnt_4.c", "bitfiles.c", "bitstrng.c", "bstr_i.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "automotive_qsort": {
        "subdir":       "automotive/qsort",
        "binary_name":  "qsort_small",
        "small_srcs":   ["qsort_small.c"],
        "large_srcs":   ["qsort_large.c"],
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "automotive_susan": {
        "subdir":       "automotive/susan",
        "binary_name":  "susan",
        "small_srcs":   ["susan.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "network_dijkstra": {
        "subdir":       "network/dijkstra",
        "binary_name":  "dijkstra_small",
        "small_srcs":   ["dijkstra_small.c"],
        "large_srcs":   ["dijkstra_large.c"],
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "network_patricia": {
        "subdir":       "network/patricia",
        "binary_name":  "patricia",
        # patricia.c is the library, patricia_test.c has main()
        # Needs tirpc headers on modern Linux
        "small_srcs":   ["patricia_test.c", "patricia.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  ["-I/usr/include/tirpc"],
        "extra_ldflags": ["-ltirpc"],
        "success_exit_codes": [0, 1],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "security_blowfish": {
        "subdir":       "security/blowfish",
        "binary_name":  "bf",
        # bf_encrypt.c / bfspeed.c / bftest.c are standalone test drivers
        "small_srcs":   ["bf.c", "bf_cbc.c", "bf_cfb64.c", "bf_ecb.c",
                         "bf_enc.c", "bf_ofb64.c", "bf_skey.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0, 1],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "security_rijndael": {
        "subdir":       "security/rijndael",
        "binary_name":  "rijndael",
        # aesxam.c has main(), aes.c is the library
        # Needs gnu89 for old-style C
        "small_srcs":   ["aesxam.c", "aes.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  ["-std=gnu89"],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {
            "aesxam.c": [
                (
                    "    fpos_t          flen;\n",
                    "    long            flen;\n",
                ),
                (
                    "    fgetpos(fin, &flen);            /* and then reset to start          */\n",
                    "    flen = ftell(fin);              /* and then reset to start          */\n",
                ),
            ],
        },
    },
    "security_sha": {
        "subdir":       "security/sha",
        "binary_name":  "sha",
        "small_srcs":   ["sha_driver.c", "sha.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "telecomm_adpcm": {
        "subdir":       "telecomm/adpcm",
        "binary_name":  "rawcaudio",
        "small_srcs":   ["rawcaudio.c", "adpcm.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
    "telecomm_crc32": {
        "subdir":       "telecomm/CRC32",
        "binary_name":  "crc",
        "small_srcs":   ["crc_32.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": ["../adpcm/data/large.pcm"],
        "source_fixes": {},
    },
    "telecomm_fft": {
        "subdir":       "telecomm/FFT",
        "binary_name":  "fft",
        "small_srcs":   ["main.c", "fftmisc.c", "fourierf.c"],
        "large_srcs":   None,
        "shared_srcs":  [],
        "extra_cflags":  [],
        "extra_ldflags": [],
        "success_exit_codes": [0],
        "extra_input_paths": [],
        "source_fixes": {},
    },
}

SIMPLE_CATEGORIES = {"automotive", "network", "security", "telecomm"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clone_repo(repo_url: str, dest: Path) -> Path:
    if dest.exists():
        print(f"  Reusing existing clone at {dest}")
        return dest
    print(f"  Cloning {repo_url} -> {dest} ...")
    result = subprocess.run(
        ["git", "clone", "--depth=1", repo_url, str(dest)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr}")
    return dest


def find_input_files(src_dir: Path, extra_input_paths: list[str] | None = None) -> list[Path]:
    exts  = ("*.pgm", "*.ppm", "*.txt", "*.asc", "*.bin",
             "*.wav", "*.pcm", "*.dat", "*.udp", "*.adpcm")
    files = []
    for ext in exts:
        files.extend(src_dir.glob(ext))
        if (src_dir / "data").exists():
            files.extend((src_dir / "data").glob(ext))
        if (src_dir.parent / "data").exists():
            files.extend((src_dir.parent / "data").glob(ext))
    for rel_path in extra_input_paths or []:
        candidate = (src_dir / rel_path).resolve()
        if candidate.exists() and candidate.is_file():
            files.append(candidate)
    return sorted(set(files))


def large_binary_name_for(binary_name: str) -> str:
    if "_small" in binary_name:
        return binary_name.replace("_small", "_large")
    return binary_name + "_large"


def binary_name_for_size(cfg: dict, size: str) -> str:
    if size == "large" and cfg.get("large_srcs") not in (None, [], cfg.get("small_srcs")):
        return large_binary_name_for(cfg["binary_name"])
    return cfg["binary_name"]


def maybe_fix_source_text(text: str, benchmark_key: str, fname: str, cfg: dict) -> str:
    fixed = text
    for old, new in cfg.get("source_fixes", {}).get(fname, []):
        if old not in fixed:
            raise RuntimeError(
                f"Source fix for {benchmark_key}/{fname} did not match expected text"
            )
        fixed = fixed.replace(old, new, 1)
    return fixed


def copy_text_with_fixes(src: Path, dest: Path, benchmark_key: str, cfg: dict):
    text = src.read_text(errors="replace")
    dest.write_text(maybe_fix_source_text(text, benchmark_key, src.name, cfg))


def build_compile_sh(cfg: dict, benchmark_key: str) -> str:
    """Generate compile.sh using per-file compilation."""
    binary = cfg["binary_name"]
    small  = cfg["small_srcs"]
    large  = cfg["large_srcs"]
    extra  = " ".join(cfg.get("extra_cflags", []))

    lines = ["#!/bin/bash", f"# Compile script for {benchmark_key}", "set -e", ""]

    def emit_build(srcs: list[str], out_name: str):
        lines.append(f"# ── build {out_name} ──")
        asm_outs = [f"asm/{Path(s).stem}.s" for s in srcs]
        obj_outs = [f"{Path(s).stem}.o"     for s in srcs]
        lines.append("mkdir -p asm pred_asm")
        for src, asm in zip(srcs, asm_outs):
            lines.append(
                f"gcc -O2 -S -march=x86-64 -masm=att {extra} "
                f"-o {asm} src/{src}"
            )
        lines.append("")
        for asm, obj in zip(asm_outs, obj_outs):
            lines.append(f"gcc -c -o {obj} {asm}")
        obj_list  = " ".join(obj_outs)
        ld_extras = " ".join(cfg.get("extra_ldflags", []))
        lines.append(f"gcc -o {out_name} {obj_list} -lm {ld_extras}")
        lines.append(f'echo "Built {out_name}"')
        lines.append("")

    emit_build(small, binary)

    if large is not None and large != small:
        large_binary = large_binary_name_for(binary)
        emit_build(large, large_binary)

    return "\n".join(lines) + "\n"


def build_run_script(
    script_path:   Path | None,
    cfg:           dict,
    benchmark_key: str,
    size:          str,
    repo_root:     Path,
) -> str:
    """
    Build a normalised run script that references input files from input/
    and calls the binary by its correct name.
    Known input filenames are rewritten to input/<filename>.
    Output filenames are left untouched.
    """
    binary = binary_name_for_size(cfg, size)

    # Special cases with stdin redirection
    if benchmark_key == "telecomm_adpcm":
        pcm = "small.pcm" if size == "small" else "large.pcm"
        out = f"output_{size}.adpcm"
        return f"#!/bin/sh\n./{binary} < input/{pcm} > {out}\n"

    if benchmark_key == "telecomm_crc32":
        return f"#!/bin/sh\n./{binary} input/large.pcm > output_{size}.txt\n"

    if script_path is None or not script_path.exists():
        return f"#!/bin/sh\n./{binary}\n"

    content = script_path.read_text(errors="replace")

    # Build set of known input filenames from repo
    src_dir      = repo_root / cfg["subdir"]
    data_exts    = {".pgm", ".ppm", ".asc", ".dat", ".udp",
                    ".pcm", ".adpcm", ".bin", ".wav"}
    known_inputs = set()
    for search in [src_dir, src_dir / "data", src_dir.parent / "data",
                   src_dir.parent]:
        if search.exists():
            for f in search.iterdir():
                if f.is_file() and f.suffix in data_exts:
                    known_inputs.add(f.name)

    patched = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            patched.append(line)
            continue

        # Normalise binary call to ./binary_name
        line = re.sub(
            rf"(?<![./\w]){re.escape(binary)}\b|\./{re.escape(binary)}\b",
            f"./{binary}",
            line,
            count=1,
        )

        # Rewrite ONLY known input filenames to input/<filename>
        # Never rewrite output files
        for fname in known_inputs:
            line = re.sub(
                rf"(?<![/\w]){re.escape(fname)}\b",
                f"input/{fname}",
                line,
            )

        patched.append(line)

    return "\n".join(patched) + "\n"


def write_problem_dir(
    out_root:      Path,
    benchmark_key: str,
    cfg:           dict,
    src_dir:       Path,
    repo_root:     Path,
) -> Path:
    problem_dir = out_root / benchmark_key
    problem_dir.mkdir(parents=True, exist_ok=True)

    all_srcs = sorted(set(
        (cfg["small_srcs"] or []) +
        (cfg["large_srcs"] or []) +
        (cfg["shared_srcs"] or [])
    ))

    # ── src/ ────────────────────────────────────────────────────────────────
    src_out = problem_dir / "src"
    src_out.mkdir(exist_ok=True)
    for fname in all_srcs:
        candidates = [src_dir / fname, src_dir / "src" / fname]
        for fpath in candidates:
            if fpath.exists():
                copy_text_with_fixes(fpath, src_out / fname, benchmark_key, cfg)
                break
        else:
            print(f"  [!] WARNING: {fname} not found near {src_dir}")
    for h in src_dir.glob("*.h"):
        copy_text_with_fixes(h, src_out / h.name, benchmark_key, cfg)
    src_subdir = src_dir / "src"
    if src_subdir.exists():
        for h in src_subdir.glob("*.h"):
            copy_text_with_fixes(h, src_out / h.name, benchmark_key, cfg)

    # ── input/ ──────────────────────────────────────────────────────────────
    inp_out = problem_dir / "input"
    inp_out.mkdir(exist_ok=True)
    for f in find_input_files(src_dir, cfg.get("extra_input_paths")):
        try:
            shutil.copy2(f, inp_out / f.name)
        except Exception:
            pass

    # ── asm/ and pred_asm/ ──────────────────────────────────────────────────
    (problem_dir / "asm").mkdir(exist_ok=True)
    (problem_dir / "pred_asm").mkdir(exist_ok=True)

    # ── compile.sh ──────────────────────────────────────────────────────────
    compile_sh = build_compile_sh(cfg, benchmark_key)
    (problem_dir / "compile.sh").write_text(compile_sh)
    os.chmod(problem_dir / "compile.sh", 0o755)

    # ── run scripts ─────────────────────────────────────────────────────────
    small_script = next(
        (p for p in [src_dir / "runme_small.sh",
                     src_dir.parent / "runme_small.sh"] if p.exists()), None
    )
    large_script = next(
        (p for p in [src_dir / "runme_large.sh",
                     src_dir.parent / "runme_large.sh"] if p.exists()), None
    )

    (problem_dir / "run_small.sh").write_text(
        build_run_script(small_script, cfg, benchmark_key, "small", repo_root)
    )
    (problem_dir / "run_large.sh").write_text(
        build_run_script(large_script, cfg, benchmark_key, "large", repo_root)
    )
    os.chmod(problem_dir / "run_small.sh", 0o755)
    os.chmod(problem_dir / "run_large.sh", 0o755)

    # ── pred.s placeholder ──────────────────────────────────────────────────
    entry  = cfg["small_srcs"][0] if cfg["small_srcs"] else "unknown"
    pred_s = problem_dir / "pred.s"
    pred_s.write_text(
        f"# Transpiler entry point for {benchmark_key}\n"
        f"# Primary source: {entry}\n"
        f"# See asm/ for per-file x86 assembly\n"
        f"# Write per-file ARM64/RISC-V output to pred_asm/\n"
    )

    # ── metadata ────────────────────────────────────────────────────────────
    category = benchmark_key.split("_")[0]
    metadata = {
        "benchmark":     benchmark_key.split("_", 1)[1],
        "category":      category,
        "benchmark_key": benchmark_key,
        "binary_name":   cfg["binary_name"],
        "small_srcs":    cfg["small_srcs"],
        "large_srcs":    cfg["large_srcs"],
        "shared_srcs":   cfg["shared_srcs"],
        "all_srcs":      all_srcs,
        "asm_files":     [f"{Path(s).stem}.s" for s in (cfg["small_srcs"] or [])],
        "asm_files_large": (
            [f"{Path(s).stem}.s" for s in cfg["large_srcs"]]
            if cfg["large_srcs"] else None
        ),
        "entry_src":     entry,
        "subdir":        cfg["subdir"],
        "extra_cflags":  cfg.get("extra_cflags",  []),
        "extra_ldflags": cfg.get("extra_ldflags", []),
        "success_exit_codes": cfg.get("success_exit_codes", [0]),
        "extra_input_paths": cfg.get("extra_input_paths", []),
    }
    (problem_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    return problem_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare MiBench into HumanEval-style problem dirs."
    )
    parser.add_argument("--repo",       default="https://github.com/embecosm/mibench")
    parser.add_argument("--clone-dir",  default=str(PROJECT_ROOT / "mibench_repo"))
    parser.add_argument("--out",        default=str(PROJECT_ROOT / "mibench_problems"))
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=["automotive", "network", "security", "telecomm",
                 "consumer", "office"],
        default=list(SIMPLE_CATEGORIES),
    )
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    out_root  = Path(args.out)
    clone_dir = Path(args.clone_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Checking MiBench repo...")
    try:
        repo_root = clone_repo(args.repo, clone_dir)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    if args.all:
        selected_keys = list(BENCH_CONFIG.keys())
    else:
        selected_keys = [
            k for k in BENCH_CONFIG
            if k.split("_")[0] in args.categories
        ]

    print(f"\n[2/3] Writing {len(selected_keys)} benchmarks to {out_root}/\n")

    index_records = []

    for key in tqdm(selected_keys, desc="Preparing"):
        cfg     = BENCH_CONFIG[key]
        src_dir = repo_root / cfg["subdir"]

        if not src_dir.exists():
            tqdm.write(f"  [!] SKIP {key} — dir not found: {src_dir}")
            continue

        problem_dir = write_problem_dir(out_root, key, cfg, src_dir, repo_root)

        n_small = len(cfg["small_srcs"] or [])
        n_large = len(cfg["large_srcs"] or []) if cfg["large_srcs"] else 0
        tqdm.write(
            f"  [✓] {key:30s}  binary={cfg['binary_name']:20s} "
            f"small={n_small}"
            + (f"  large={n_large}" if n_large else "")
            + (f"  cflags={cfg['extra_cflags']}" if cfg.get("extra_cflags") else "")
        )

        index_records.append({
            "benchmark_key": key,
            "category":      key.split("_")[0],
            "binary_name":   cfg["binary_name"],
            "problem_dir":   str(problem_dir.relative_to(out_root)),
        })

    (out_root / "index.json").write_text(json.dumps(index_records, indent=2))

    print(f"\n[3/3] Done. {len(index_records)} benchmarks in {out_root}/\n")
    print("Next steps:")
    print(
        "  python mibench_transpiler_evaluation/compile_mibench_asm.py "
        f"--root {out_root} --mode linux-x64"
    )
    print(
        "  python mibench_transpiler_evaluation/gen_reference_mibench.py "
        f"--root {out_root} --repo {clone_dir}"
    )


if __name__ == "__main__":
    main()
