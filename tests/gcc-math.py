#!/usr/bin/env python3
"""Check the interactive GCC convenience with real linking and exact arguments."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


REPO = Path(__file__).resolve().parent.parent
TIMEOUT = 60
SOURCE = r"""#include <stdio.h>
#include <math.h>
#define max 2177280

int is_val(int n){
    int x = n, temp = 0;
    while(x){
        temp += (int)(pow(x % 10,5));
        x /= 10;
    }
    return temp == n;
}

int main(){
    int sum = 0;
    int count = 1;
    for(int i = 2;i <= max; i++){
        if(!is_val(i)) continue;
        count += 1;
        printf("%d:%d\n",count,i);
        sum += i;
    }

    printf("sum = %d\n",sum);
    return 0;
}
"""
EXPECTED = "2:4150\n3:4151\n4:54748\n5:92727\n6:93084\n7:194979\nsum = 443839\n"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def note(message):
    print("[gcc math test] " + message, flush=True)


def zsh(root, script, *arguments, extra_env=None):
    home = root / "home"
    home.mkdir(exist_ok=True)
    environment = dict(os.environ, HOME=str(home), ZDOTDIR=str(home),
                       XDG_CACHE_HOME=str(home / ".cache"), LC_ALL="C.UTF-8",
                       TERM="xterm-256color")
    environment.pop("LINUX_ENV_GCC_AUTO_MATH", None)
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        ["zsh", "-dfi", "-c", 'source "$1/config/zshrc"\nshift\n' + script,
         "gcc-math-test", str(REPO), *map(str, arguments)],
        cwd=root, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)


def succeeds(result, message):
    require(result.returncode == 0,
            message + "\nstdout:\n" + result.stdout + "\nstderr:\n" + result.stderr)
    return result


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_compiler(root):
    note("Compile the original pow example as gcc 8.digit.c and run ./a.out")
    source = root / "8.digit.c"
    source.write_text(SOURCE)
    spaced = root / "directory with spaces"
    spaced.mkdir()
    spaced_source = spaced / "digit example.c"
    spaced_source.write_text(SOURCE)
    invalid = root / "invalid.c"
    invalid.write_text("int main(void) { undeclared_name; }\n")
    before = {path: checksum(path) for path in (source, spaced_source, invalid)}
    compiler = Path(shutil.which("gcc")).resolve()
    compiler_before = checksum(compiler)

    result = succeeds(zsh(root, 'gcc 8.digit.c && ./a.out'), "Default math link failed")
    require((root / "a.out").is_file(), "Plain gcc did not create a.out")
    require(result.stdout == EXPECTED, "Unexpected original-program output: " + result.stdout)

    note("Keep explicit output names, space-containing paths, and explicit -lm working")
    output = spaced / "custom program"
    result = succeeds(zsh(root, 'gcc "$1" -o "$2" && "$2"', spaced_source, output),
                      "Quoted source/output paths were damaged")
    require(output.is_file() and result.stdout == EXPECTED, "Custom output did not run correctly")
    result = succeeds(zsh(root, 'gcc 8.digit.c -o explicit-math -lm && ./explicit-math'),
                      "An explicit math library stopped working")
    require(result.stdout == EXPECTED, "Explicit -lm changed the result")

    note("Support separate object compilation, syntax checks, and compiler queries")
    result = succeeds(zsh(root, 'gcc -c 8.digit.c -o digit.o && '
                         'gcc digit.o -o object-program && ./object-program'),
                      "Separate compilation/linking failed")
    require((root / "digit.o").is_file() and result.stdout == EXPECTED,
            "Linking a precompiled object failed")
    succeeds(zsh(root, 'gcc -fsyntax-only 8.digit.c'), "Syntax-only check failed")
    for arguments in (("--version",), ("-dumpmachine",), ("-print-file-name=libm.so",)):
        wrapped = succeeds(zsh(root, 'gcc "$@"', *arguments), "Wrapped query failed")
        raw = succeeds(zsh(root, 'command gcc "$@"', *arguments), "Raw query failed")
        require(wrapped.stdout == raw.stdout, "Compiler query output changed: " + repr(arguments))

    note("Keep errors, the opt-out, and command gcc independent of the convenience")
    raw = zsh(root, 'command gcc 8.digit.c -o raw-without-math')
    require(raw.returncode != 0 and "pow" in raw.stderr,
            "The raw system GCC unexpectedly linked pow without -lm: " + raw.stderr)
    require(not (root / "raw-without-math").exists(), "Failed raw link left an executable")
    disabled = zsh(root, 'LINUX_ENV_GCC_AUTO_MATH=0 gcc 8.digit.c -o disabled-math')
    require(disabled.returncode == raw.returncode and "pow" in disabled.stderr,
            "LINUX_ENV_GCC_AUTO_MATH=0 did not restore the raw linker failure")
    wrapped_error = zsh(root, 'gcc invalid.c -o invalid-program')
    raw_error = zsh(root, 'command gcc invalid.c -o invalid-program')
    require(wrapped_error.returncode == raw_error.returncode != 0
            and "error:" in wrapped_error.stderr,
            "The convenience hid or changed a failed compiler exit status")
    require(not (root / "invalid-program").exists(), "Failed compilation created a program")
    require(all(checksum(path) == digest for path, digest in before.items()),
            "The compiler convenience modified a source file")
    require(checksum(compiler) == compiler_before, "The system compiler executable was modified")


def test_arguments(root):
    note("Record exact compiler arguments, bypasses, and failure status with a fake GCC")
    fakebin = root / "fake-bin"
    fakebin.mkdir()
    fake = fakebin / "gcc"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['LINUX_ENV_FAKE_GCC_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(os.environ.get('LINUX_ENV_FAKE_GCC_STATUS', '0')))\n")
    fake.chmod(0o755)
    log = root / "arguments.jsonl"
    cases = [
        ("plain source", ["8.digit.c"], True, "gcc"),
        ("quoted arguments", ["source with spaces.c", "-o", "output with spaces",
                              "-DNAME=hello world", "-DVALUE=$HOME", "-DVALUE2=*.c"], True, "gcc"),
        ("link objects", ["first.o", "second object.o", "-o", "program"], True, "gcc"),
        ("explicit library", ["-lm", "8.digit.c", "-o", "program"], True, "gcc"),
        ("verbose compilation", ["-v", "8.digit.c"], True, "gcc"),
        ("dumpbase compilation", ["-dumpbase", "foo", "8.digit.c"], True, "gcc"),
        ("stdin compilation", ["-x", "c", "-"], True, "gcc"),
        ("library link input", ["-lexample"], True, "gcc"),
        ("linker input options", ["-Wl,--whole-archive,example.a,--no-whole-archive"], True, "gcc"),
        ("compile dependency flags", ["-MMD", "-MP", "8.digit.c"], True, "gcc"),
        ("response file", ["@arguments with spaces"], True, "gcc"),
        ("no arguments", [], False, "gcc"),
        ("verbose query", ["-v"], False, "gcc"),
        ("verbose options query", ["-v", "-Q"], False, "gcc"),
        ("driver dry run without inputs", ["-###"], False, "gcc"),
        ("long verbose query", ["--verbose"], False, "gcc"),
    ]
    skipped = ["-c", "--compile", "-S", "--assemble", "-E", "--preprocess", "-M", "-MM",
               "-fsyntax-only", "-r", "-nostdlib", "--no-standard-libraries",
               "-nodefaultlibs", "-nolibc", "-ffreestanding"]
    for flag in skipped:
        cases.append(("passthrough " + flag, ["-Wall", "8.digit.c", flag,
                                             "-o", "output with spaces"], False, "gcc"))
    queries = ["--version", "--help", "--help=common", "--target-help", "-dumpmachine",
               "-dumpversion", "-dumpfullversion", "-dumpspecs", "-print-search-dirs",
               "-print-file-name=libm.so", "-print-prog-name=ld"]
    for flag in queries:
        cases.append(("query " + flag, [flag], False, "gcc"))
    cases += [
        ("per-command opt-out", ["8.digit.c", "-o", "disabled output"], False,
         "LINUX_ENV_GCC_AUTO_MATH=0 gcc"),
        ("direct compiler bypass", ["8.digit.c"], False, "command gcc"),
        ("normal after opt-out", ["8.digit.c"], True, "gcc"),
    ]
    script = ['path=("$1" $path)', "shift", "rehash"]
    expected = []
    for label, arguments, adds_math, command in cases:
        script.append(command + " " + shlex.join(arguments) + " || exit 80")
        expected.append((label, arguments + (["-lm"] if adds_math else [])))
    script += [
        "export LINUX_ENV_FAKE_GCC_STATUS=37",
        "gcc failing.c",
        "[[ $? == 37 ]] || exit 81",
        "gcc -c failing.c",
        "[[ $? == 37 ]] || exit 82",
        "LINUX_ENV_GCC_AUTO_MATH=0 gcc failing.c",
        "[[ $? == 37 ]] || exit 83",
        "command gcc failing.c",
        "[[ $? == 37 ]] || exit 84",
        "exit 0",
    ]
    expected += [("link failure", ["failing.c", "-lm"]),
                 ("compile failure", ["-c", "failing.c"]),
                 ("disabled failure", ["failing.c"]),
                 ("bypass failure", ["failing.c"])]
    succeeds(zsh(root, "\n".join(script), fakebin,
                 extra_env={"LINUX_ENV_FAKE_GCC_LOG": str(log)}),
             "A compiler invocation changed its exit status")
    observed = [json.loads(line) for line in log.read_text().splitlines()]
    require(len(observed) == len(expected), "An invocation disappeared or called GCC more than once")
    for actual, (label, wanted) in zip(observed, expected):
        require(actual == wanted,
                label + " changed argv: expected " + repr(wanted) + ", got " + repr(actual))


def main():
    require(shutil.which("zsh") is not None, "Install zsh before running this test")
    require(shutil.which("gcc") is not None, "Install gcc before running this test")
    with tempfile.TemporaryDirectory(prefix="linux-env-gcc-math-") as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        test_real_compiler(real)
        fake = root / "fake"
        fake.mkdir()
        test_arguments(fake)
    note("All interactive GCC math-link checks passed")


if __name__ == "__main__":
    main()
