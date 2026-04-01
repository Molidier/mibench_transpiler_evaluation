# MiBench Transpiler Evaluation

This directory contains the MiBench preparation, compilation, reference-generation, and predicted-assembly evaluation scripts.

Scripts:
- `prepare_mibench.py`: clone/reuse MiBench and generate `mibench_problems/`
- `compile_mibench_asm.py`: compile benchmark C sources into x86 assembly in `asm/`
- `gen_reference_mibench.py`: build and run the x86 assembly to save reference outputs
- `eval_pred_mibench.py`: assemble/link/run translated `pred_asm/*.s` and compare against references

All four scripts resolve their default paths against the repository root, so they work whether you run them from the repo root or from inside `mibench_transpiler_evaluation/`.

## Repository Layout

Expected directories in the repository root:
- `mibench_repo/`: MiBench source clone
- `mibench_problems/`: generated benchmark problem directories
- `results_mibench_asm.json`: compile results
- `results_reference.json`: Linux reference-generation results
- `results_arm64.json` / `results_riscv64.json`: Mac evaluation results

## Linux x86-64 Workflow

Use Linux x86-64 to prepare the benchmark set, compile source files into x86 assembly, and generate the reference outputs.

Recommended commands from the repository root:

```bash
rm -rf mibench_problems
python mibench_transpiler_evaluation/prepare_mibench.py
python mibench_transpiler_evaluation/compile_mibench_asm.py --mode linux-x64
python mibench_transpiler_evaluation/gen_reference_mibench.py
```

What success looks like:
- `prepare_mibench.py`: `Done. 12 benchmarks in .../mibench_problems/`
- `compile_mibench_asm.py`: `Results : 12/12 passed`
- `gen_reference_mibench.py`: `Results : 12/12 passed`

Optional checks:

```bash
rg '"status": "FAIL"' results_mibench_asm.json results_reference.json
```

That command should print nothing.

## Mac Apple Silicon Workflow

Use an Apple Silicon Mac to evaluate translated assembly in `pred_asm/` after the Linux preparation steps are complete.

### ARM64 Evaluation

Install Apple command line tools:

```bash
xcode-select --install
```

Run evaluation:

```bash
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch arm64
```

This reads:
- `mibench_problems/*/pred_asm/*.s`
- `mibench_problems/*/reference/*`

and writes:
- `results_arm64.json`

### RISC-V Evaluation on Mac

There are two supported RISC-V backends. The script auto-selects the first usable one it finds.

Option 1: Spike + pk via the RISC-V Homebrew tap

```bash
brew tap riscv-software-src/riscv
brew install riscv-tools
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch riscv64
```

Option 2: Linux-user QEMU with a RISC-V Linux cross-toolchain

Requirements:
- `qemu-riscv64`
- one of `riscv64-linux-gnu-gcc` or `riscv64-unknown-linux-gnu-gcc`

Then run:

```bash
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch riscv64
```

The result file is:
- `results_riscv64.json`

## Common Usage Patterns

Evaluate one benchmark only:

```bash
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch arm64 --benchmark automotive_basicmath
```

Skip large-input runs:

```bash
python mibench_transpiler_evaluation/gen_reference_mibench.py --no-large
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch arm64 --no-large
```

Allow a small stdout diff tolerance:

```bash
python mibench_transpiler_evaluation/eval_pred_mibench.py --arch arm64 --diff-tolerance 2
```

## Device Summary

Use Linux x86-64 for:
- `prepare_mibench.py`
- `compile_mibench_asm.py --mode linux-x64`
- `gen_reference_mibench.py`

Use Apple Silicon Mac for:
- `eval_pred_mibench.py --arch arm64`
- `eval_pred_mibench.py --arch riscv64`

## Notes

- Run the Linux steps sequentially. Do not run `compile_mibench_asm.py` and `gen_reference_mibench.py` in parallel.
- `eval_pred_mibench.py` expects real translated assembly in `pred_asm/`; it will fail on the placeholder files created by `compile_mibench_asm.py`.
- ARM64 evaluation is intentionally restricted to Apple Silicon macOS hosts.
