import subprocess  # nosec B404
import shutil


def build_rust() -> None:
    print("Building Rust Risk Engine...")
    try:
        cargo_path = shutil.which("cargo")
        if not cargo_path:
            print("[FAIL] Cargo compiler not found in PATH.")
            return

        subprocess.run(
            [
                cargo_path,
                "build",
                "--release",
                "--manifest-path",
                "nexus/polyglot/rust_risk_engine/Cargo.toml",
            ],
            check=True,
        )  # nosec B603
        print("[OK] Rust engine built.")
    except Exception as e:
        print(f"[FAIL] Rust build failed: {e}")


def build_go() -> None:
    print("Building Go Auditor...")
    try:
        go_path = shutil.which("go")
        if not go_path:
            print("[FAIL] Go compiler not found in PATH.")
            return

        subprocess.run(
            [go_path, "build", "-o", "auditor.exe", "main.go"],  # nosec B603
            cwd="nexus/polyglot/go_auditor",
            check=True
        )
        print("[OK] Go auditor built.")
    except Exception as e:
        print(f"[FAIL] Go build failed: {e}")


def build_zig() -> None:
    print("Building Zig Validator...")
    try:
        zig_path = shutil.which("zig")
        if not zig_path:
            print("[FAIL] Zig compiler not found in PATH.")
            return

        subprocess.run(
            [zig_path, "build-exe", "main.zig", "-O", "ReleaseFast"],  # nosec B603
            cwd="nexus/polyglot/zig_validator",
            check=True
        )
        print("[OK] Zig validator built.")
    except Exception as e:
        print(f"[FAIL] Zig build failed: {e}")


if __name__ == "__main__":
    print("Nexus Polyglot Build Orchestrator")
    print("=" * 40)
    build_rust()
    build_go()
    build_zig()
    print("=" * 40)
    print("Build process completed.")
