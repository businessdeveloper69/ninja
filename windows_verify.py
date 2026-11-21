import json
import os
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path(os.environ["RUNNER_TEMP"])
FMT = ROOT / "fmt"
results = {"platform": platform.platform(), "fmt_commit": "407c905e45ad75fc29bf0f9bb7c5c2fd3475976f", "lanes": {}}


def run(*args, cwd, required=True):
    p = subprocess.run([str(a) for a in args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    if required and p.returncode:
        raise RuntimeError(f"{args}: exit {p.returncode}\n{p.stdout}")
    return p


def lane(name, candidate):
    ninja = ROOT / f"ninja-bin-{name}" / "Release" / "ninja.exe"
    build = ROOT / f"fmt build with space {name}"
    cases = results["lanes"][name] = {}
    print("BEGIN", name, flush=True)
    run("cmake", "-S", FMT, "-B", build, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}", "-DCMAKE_CXX_COMPILER=cl", "-DCMAKE_BUILD_TYPE=Release", "-DFMT_TEST=OFF", "-DFMT_DOC=OFF", "-DFMT_INSTALL=OFF", cwd=ROOT)
    listing = run(ninja, "-t", "targets", "all", cwd=build).stdout.splitlines()
    target_names = [l.rsplit(": ", 1)[0].replace("\\", "/") for l in listing if ": " in l]
    obj, = [t for t in target_names if t.lower().endswith("/src/format.cc.obj") and t.startswith("CMakeFiles/fmt.dir/")]
    arc, = [t for t in target_names if t.lower().split("/")[-1] == "fmt.lib" and not Path(t).is_absolute()]
    cases["relative_names"] = [obj, arc]

    def check(label, *args, good, cwd=build, needle=None):
        p = run(ninja, *args, cwd=cwd, required=False)
        cases[label] = {"args": list(map(str, args)), "exit": p.returncode, "output": p.stdout.strip()[:1100]}
        print(name, label, p.returncode, p.stdout.strip()[:400], flush=True)
        if (p.returncode == 0) != good or (needle and needle not in p.stdout):
            raise AssertionError((name, label, p.stdout))
        if not good and "unknown target" not in p.stdout:
            raise AssertionError((name, label, p.stdout))

    for target, label in ((obj, "object"), (arc, "archive")):
        absolute = build / target
        assert not absolute.exists()
        check(label + "_missing_absolute", absolute, good=candidate)
        assert absolute.exists() == candidate
        check(label + "_relative", target, good=True)
        assert absolute.is_file()
        check(label + "_already_built_absolute", absolute, good=candidate, needle="no work to do" if candidate else None)
        check(label + "_already_built_relative", target, good=True, needle="no work to do")

    check("forward_slashes", (build / obj).as_posix(), good=candidate)
    check("mixed_separators", str(build) + "/" + obj, good=candidate)
    for key, target in {
        "different_drive": ("Q:" if build.drive.upper() != "Q:" else "Z:") + str(build / arc)[2:],
        "drive_relative": build.drive + arc,
        "rooted_no_drive": str(build / arc)[2:],
        "sibling_same_prefix": str(build) + "-neighbor/" + arc,
        "different_filename_case": str(build / arc.upper()),
    }.items():
        check(key, "-n", target, good=False)
    for key, arg in {"drive_letter_case": str(build / arc)[0].swapcase() + str(build / arc)[1:], "directory_case": str(build).replace("fmt build", "FMT BUILD") + "/" + arc}.items():
        p = run(ninja, "-n", arg, cwd=build, required=False)
        assert p.returncode == 0 or "unknown target" in p.stdout
        cases[key] = {"args": [arg], "exit": p.returncode, "output": p.stdout.strip()}

    case = ROOT / ("manifest with spaces " + name)
    case.mkdir()
    (case / "build.ninja").write_text("root = " + case.as_posix() + "\nbuild pick: phony RELATIVE_MARKER\nbuild $root/pick: phony ABSOLUTE_MARKER\nbuild -sibling/pick: phony PREFIX_TRAP\n")
    check("explicit_absolute_manifest_node_wins", "-t", "query", case / "pick", cwd=case, good=True, needle="ABSOLUTE_MARKER")
    check("explicit_relative_manifest_node", "-t", "query", "pick", cwd=case, good=True, needle="RELATIVE_MARKER")
    check("prefix_boundary_trap", "-n", str(case)+"-sibling/pick", cwd=case, good=False)
    (build / obj).unlink()
    check("absolute_archive_rebuild_after_object_deletion", build / arc, good=candidate)
    check("relative_archive_repairs_or_current", arc, good=True)
    assert (build / obj).is_file()
    source = build / "native-consumer.cpp"
    source.write_text('#include <fmt/format.h>\n#include <iostream>\nint main(){auto text=fmt::format("value={:04d}",37);std::cout<<text;return text=="value=0037"?0:1;}\n')
    run("cl", "/nologo", "/EHsc", "/std:c++17", "/utf-8", "/MD", "/I" + str(FMT/"include"), source, build / arc, "/Fe:verify-consumer.exe", cwd=build)
    value = run(build/"verify-consumer.exe", cwd=build).stdout.strip()
    assert value == "value=0037"
    cases["native_consumer"] = value


try:
    lane("base", False)
    lane("candidate", True)
finally:
    print("NINJA_2720_WINDOWS_JSON=" + json.dumps(results, sort_keys=True), flush=True)
