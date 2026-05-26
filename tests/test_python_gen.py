"""Tests for the Python CRC code generator.

Python is the only target whose generated code can be exec'd directly
in the test process -- no external toolchain needed -- so every test
here is fast and unmarked (the `slow` marker is reserved for tests
that shell out to gcc / rustc / ghdl; see test_c_gen.py etc.).
"""

from __future__ import annotations

import pytest

from crcglot import CRC_CATALOGUE, generate_python


# Standard check string used by the reveng catalogue
CHECK_DATA = b"123456789"


class TestGeneratePython:
    """Verify generated Python code computes correct CRC values."""

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_generated_code_matches_check(self, name):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name)
        assert code is not None, f"generate_python returned code for {name}"

        # Act - execute the generated function
        ns: dict = {}
        exec(code, ns)
        func_name = name.replace("-", "_").replace(".", "_")
        actual = ns[func_name](CHECK_DATA)

        # Assert
        assert actual == expected, f"{name}: {actual:#x} != {expected:#x}"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_python("nonexistent") is None, "unknown algorithm should return None"

    def test_has_docstring(self):
        # Act
        code = generate_python("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert '"""' in code, "has docstring"
        assert "crc16-modbus" in code, "names the algorithm"

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_matches_check(self, name):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name, table=True)
        assert code is not None, f"generate_python(table=True) returned code for {name}"

        # Act - execute the generated table-driven function
        ns: dict = {}
        exec(code, ns)
        func_name = name.replace("-", "_").replace(".", "_")
        actual = ns[func_name](CHECK_DATA)

        # Assert
        assert actual == expected, f"{name} table: {actual:#x} != {expected:#x}"


class TestGeneratedPythonSelfTest:
    """The generated module must expose ``<fname>_self_test()`` so a
    downstream caller can verify on their interpreter that the
    algorithm reproduces the reveng catalogue's canonical check value.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_returns_true(self, name, table):
        # Arrange
        code = generate_python(name, table=table)
        assert code is not None, f"generate_python({name!r}) returned code"
        ns: dict = {}
        exec(code, ns)
        fname = name.replace("-", "_").replace(".", "_")

        # Act
        actual = ns[f"{fname}_self_test"]()

        # Assert
        assert actual is True, (
            f"{name} (table={table}): self_test returned "
            f"{actual!r}, expected True"
        )

    def test_self_test_detects_broken_implementation(self):
        # Arrange - generate crc32 then corrupt the finalize xorout so
        # the implementation produces a wrong value; the self-test
        # must catch it (otherwise it isn't actually verifying anything).
        code = generate_python("crc32")
        assert code is not None, "crc32 generator returned code"
        corrupted = code.replace("state ^ 0xFFFFFFFF", "state ^ 0xDEADBEEF")
        assert corrupted != code, "corruption substitution actually changed something"
        ns: dict = {}
        exec(corrupted, ns)

        # Act
        actual = ns["crc32_self_test"]()

        # Assert
        assert actual is False, (
            f"self_test on corrupted crc32 returned {actual!r}, expected False"
        )


class TestGeneratedPythonStreaming:
    """The streaming primitives (init / update / finalize) must satisfy
    the splittability invariant: for any input, computing in chunks
    must produce the same result as the one-shot wrapper, which in
    turn must equal the reveng catalogue's check value.

    Three patterns are exercised per algorithm:

    1. **Split mid-input** (init -> update("1234") -> update("56789") ->
       finalize) -- catches wrong state shape, broken update
       accumulator, or accidentally re-applying finalize logic in update.
    2. **Empty chunk at start** (init -> update(b"") -> update(full)
       -> finalize) -- catches loops that misbehave on zero-length
       input.
    3. **Empty chunk at end** (init -> update(full) -> update(b"") ->
       finalize) -- catches a different class of zero-length bug.

    All three must equal the reveng check value AND equal the
    one-shot wrapper's result.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_streaming_matches_oneshot(self, name, table):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name, table=table)
        assert code is not None, f"generate_python({name!r}) returned code"

        ns: dict = {}
        exec(code, ns)
        fname = name.replace("-", "_").replace(".", "_")
        init_fn = ns[f"{fname}_init"]
        update_fn = ns[f"{fname}_update"]
        finalize_fn = ns[f"{fname}_finalize"]

        # Pattern 1 -- split at byte 4
        state = init_fn()
        state = update_fn(state, b"1234")
        state = update_fn(state, b"56789")
        split_result = finalize_fn(state)

        # Pattern 2 -- empty chunk first
        state = init_fn()
        state = update_fn(state, b"")
        state = update_fn(state, b"123456789")
        empty_first_result = finalize_fn(state)

        # Pattern 3 -- empty chunk last
        state = init_fn()
        state = update_fn(state, b"123456789")
        state = update_fn(state, b"")
        empty_last_result = finalize_fn(state)

        # Assert -- all three patterns equal the reveng check value
        assert split_result == expected, (
            f"{name} (table={table}): split-at-4 streamed result "
            f"{split_result:#x} != check {expected:#x}"
        )
        assert empty_first_result == expected, (
            f"{name} (table={table}): empty-chunk-first streamed result "
            f"{empty_first_result:#x} != check {expected:#x}"
        )
        assert empty_last_result == expected, (
            f"{name} (table={table}): empty-chunk-last streamed result "
            f"{empty_last_result:#x} != check {expected:#x}"
        )
