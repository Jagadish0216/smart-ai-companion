"""Record final read-only Git checks and compare active files to the saved baseline."""
import hashlib
import json
import subprocess
import zipfile
from build import ROOT, OUT, FROZEN_PARTS


def write_inventory():
    commands = [
        ["git", "branch", "--show-current"],
        ["git", "status", "--short"],
        ["git", "diff", "--check"],
        ["git", "diff", "--stat"],
    ]
    records = []
    for command in commands:
        result = subprocess.run(command, cwd=ROOT.parents[1], capture_output=True,
                                text=True, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        records.append({"command": " ".join(command), "exit_code": result.returncode,
                        "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == 0, records[-1]
    assert records[0]["stdout"].strip() == "enclosure-v1"
    log = "\n".join(
        r["command"] + "\nexit_code=" + str(r["exit_code"]) + "\n" +
        (r["stdout"] or "(no output)\n") + r["stderr"] for r in records)
    (OUT / "git_final.txt").write_text(log + "\nNO COMMIT\nNO PUSH\n", encoding="utf-8")

    baseline = {k.replace("\\", "/"): v for k, v in
                json.loads((OUT / "revision_baseline.json").read_text()).items()}
    with zipfile.ZipFile(ROOT / "history/before_downfiring_revision.zip") as archive:
        for name, digest in baseline.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest, name
    excluded = {"output/changed_files.json", "output/package_validation.json",
                "output/SHA256SUMS.txt"}
    current = {}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        name = relative.as_posix()
        if (relative.parts[0] == "history" or "__pycache__" in relative.parts or
                path.suffix == ".zip" or name in excluded):
            continue
        current[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    changed = sorted(name for name in baseline.keys() & current.keys()
                     if baseline[name] != current[name])
    added = sorted(current.keys() - baseline.keys())
    unchanged = sorted(name for name in baseline.keys() & current.keys()
                       if baseline[name] == current[name])
    archived = sorted(baseline.keys() - current.keys())
    frozen = sorted("output/" + name + ".stl" for name in FROZEN_PARTS)
    assert all(name in unchanged for name in frozen)
    source_names = lambda names: [name for name in names if "/" not in name]
    report = {
        "scope": "hardware/enclosure/ only",
        "comparison": "Beginning-of-revision SHA256 snapshot; enclosure was already untracked in Git.",
        "exclusions": "History copies, ZIPs, Python cache, this inventory and package hash manifests.",
        "original_backup": "history/before_downfiring_revision.zip; all 123 original file hashes verified",
        "modified_source_and_docs": source_names(changed),
        "added_source_and_docs": source_names(added),
        "modified_active_files": changed,
        "added_active_files": added,
        "unchanged_active_files": unchanged,
        "original_files_now_archived": archived,
        "byte_identical_head_stls": frozen,
        "final_packages": ["output/enclosure-final-project.zip", "output/enclosure-final-print-shop.zip",
                           "output/enclosure-final-fit-tests.zip"],
        "current_sha256": dict(sorted(current.items())),
        "git": records,
    }
    (OUT / "changed_files.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"modified_active": len(changed), "added_active": len(added),
            "unchanged_active": len(unchanged), "archived_originals": len(archived),
            "git_diff_check": "PASS", "branch": "enclosure-v1"}


if __name__ == "__main__":
    print(json.dumps(write_inventory(), indent=2))
