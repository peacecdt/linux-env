#!/usr/bin/env python3
"""Exercise automatic identifier menus with real INSERT keys and a native PTY."""
import json
import os
from pathlib import Path
import tempfile
import time

from identity import REPO, Terminal, require


class Editor:
    def __init__(self, root, extension, original):
        self.source = root / ("example." + extension)
        self.source.write_text(original)
        self.original = original
        self.record = root / ("state-" + extension + ".json")
        descriptor = root / ("case-" + extension + ".json")
        descriptor.write_text(json.dumps({"source": str(self.source), "output": str(self.record)}))
        self.terminal = Terminal(["vim", "-Nu", str(REPO / "config/vimrc"), "-i", "NONE", "-n",
                                  "-c", "let g:completion_case = '" + str(descriptor) + "'",
                                  "-S", str(REPO / "tests/vim-completion.vim")])
        self.serial = 0

    def __enter__(self):
        self.terminal.__enter__()
        self.settle(0.3)
        self.snapshot()
        return self

    def __exit__(self, *arguments):
        return self.terminal.__exit__(*arguments)

    def settle(self, seconds=0.25):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.terminal.pump()

    def keys(self, value):
        os.write(self.terminal.fd, value)
        self.settle()

    def snapshot(self):
        os.write(self.terminal.fd, b"\x07")
        end = time.monotonic() + 4
        while time.monotonic() < end:
            self.terminal.pump()
            try:
                state = json.loads(self.record.read_text())
            except FileNotFoundError:
                continue
            if state["serial"] > self.serial:
                self.serial = state["serial"]
                return state
        raise AssertionError("Vim did not capture its completion state")

    def wait_for(self, predicate, label):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            # A quiet interval lets the actual completion timer run between probes.
            self.settle()
            state = self.snapshot()
            if predicate(state):
                return state
        raise AssertionError(label + ": " + json.dumps(state))

    def menu(self, expected):
        state = self.wait_for(lambda s: s["pumvisible"]
                             and expected in words(s), "Identifier popup missing: " + expected)
        require(state["mode"].startswith("i"), "Completion left INSERT: " + json.dumps(state))
        return state

    def finish(self):
        self.keys(b"\x1b:qall!\r")
        self.terminal.finish()
        require(self.source.read_text() == self.original, "Completion rewrote the source on disk")


def words(state):
    return [item["word"] for item in state["completion"]["items"]]


def current(state):
    return state["lines"][state["line"] - 1]


def test_cpp(root):
    prefix = "    /* 中文 */ namespace shortname = "
    with Editor(root, "cpp", "namespace veryLongName {}\nint main() {\n"
                + prefix + "\n}\n") as editor:
        editor.keys(b"3G$av")
        short = editor.snapshot()
        require(not short["pumvisible"] and current(short).endswith("v"),
                "One character prematurely opened a completion menu")
        editor.keys(b"er")
        shown = editor.menu("veryLongName")
        require(shown["completion"]["selected"] == -1 and current(shown).endswith("ver"),
                "The unselected menu inserted text without approval")
        editor.keys(b"\t")
        selected = editor.snapshot()
        require(selected["completion"]["selected"] >= 0
                and current(selected).endswith("veryLongName"), "Tab did not select the namespace")
        editor.keys(b"\r")
        accepted = editor.snapshot()
        require(not accepted["pumvisible"] and accepted["line"] == 3
                and current(accepted) == prefix + "veryLongName" and len(accepted["lines"]) == 4,
                "Enter failed to confirm the selected namespace without adding a line")
        editor.keys(b"(")
        require(current(editor.snapshot()).endswith("veryLongName()"),
                "Accepting a word broke automatic bracket pairing")
        editor.finish()


def test_c(root):
    with Editor(root, "c", "int existingVariable = 1;\nint obsoleteVariable = 2;\n"
                "int anotherValue = 3;\nint anotherVector = 4;\nint main(void) {\n    \n}\n") as editor:
        editor.keys(b"2Gdd5G$aobs")
        deleted = editor.snapshot()
        require(not deleted["pumvisible"] and "obsoleteVariable" not in words(deleted),
                "A deleted identifier remained in the automatic suggestions")
        editor.keys(b"\x17exi")
        shown = editor.menu("existingVariable")
        require(current(shown).endswith("exi"), "Showing a variable changed the typed prefix")
        editor.keys(b"\x05")  # Native Ctrl-E dismisses without accepting a candidate.
        editor.settle(0.4)
        cancelled = editor.snapshot()
        require(not cancelled["pumvisible"] and current(cancelled).endswith("exi"),
                "A cancelled popup reopened or inserted its suggestion")
        editor.keys(b"\x7fi")  # Returning to the same prefix after a real edit must work.
        reopened = editor.menu("existingVariable")
        require(current(reopened).endswith("exi"), "Reopening changed the prefix after a real edit")
        editor.keys(b"\x05")
        editor.keys(b"\x0e")  # Native Ctrl-N remains available after cancellation.
        manual = editor.menu("existingVariable")
        require(manual["completion"]["mode"] == "keyword", "Manual Ctrl-N no longer starts keyword completion")
        # 'noselect' also applies to the initial native Ctrl-N menu.
        editor.keys(b"\t\r\r")  # Select, confirm, then create the next normally indented line.
        before_tab = editor.snapshot()
        editor.keys(b"\t")
        indented = editor.snapshot()
        require(not indented["pumvisible"] and current(indented).isspace()
                and len(current(indented)) > len(current(before_tab)),
                "Tab without a menu no longer inserts indentation")
        editor.keys(b"anotherV")
        shown = editor.menu("anotherValue")
        require("anotherVector" in words(shown), "The menu lost another matching identifier")
        editor.keys(b"\t\t\x1b[Z")  # Two selections, then terminal Shift-Tab.
        previous = editor.snapshot()
        require(previous["completion"]["selected"] == 0
                and current(previous).endswith("anotherValue"), "Shift-Tab did not select the previous word")
        editor.keys(b"\r")
        require(current(editor.snapshot()).endswith("anotherValue"), "Enter did not retain the selected C variable")
        editor.finish()


def test_python(root):
    with Editor(root, "py", "known_variable = 1\nother_variable = 2\ndef example():\n    pass\n") as editor:
        editor.keys(b"4Gccknown")
        shown = editor.menu("known_variable")
        require(shown["completion"]["selected"] == -1, "Python suggestions were selected automatically")
        editor.keys(b"\r")
        newline = editor.snapshot()
        require(newline["line"] == 5 and newline["lines"][3].strip() == "known"
                and not newline["pumvisible"], "Enter accepted an unselected suggestion instead of a newline")
        editor.keys(b"other_v")
        editor.menu("other_variable")
        editor.keys(b"\t\r[")
        paired = editor.snapshot()
        require(paired["line"] == 5 and current(paired).endswith("other_variable[]"),
                "Python completion did not preserve Enter confirmation and paired brackets")
        editor.finish()


def main():
    with tempfile.TemporaryDirectory(prefix="linux-env-completion-") as temporary:
        root = Path(temporary)
        test_cpp(root)
        test_c(root)
        test_python(root)
    print("[completion test] Native C/C++/Python menus, selection, cancellation, and pairs passed", flush=True)


if __name__ == "__main__":
    main()
