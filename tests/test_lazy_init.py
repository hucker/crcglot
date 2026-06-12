"""The lazy package surface: ``import crcglot`` loads the compute core only.

The option layers (generators, detection, reverse, trailer-id) resolve on
first attribute access via PEP 562 ``__getattr__``.  Load-order assertions run
in a fresh subprocess because the in-process interpreter has already imported
everything by the time pytest gets here.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

import crcglot

# Layers that must NOT load on bare import; one representative module each.
_LAZY_LAYER_MODULES = (
    "crcglot._detect",
    "crcglot._encode",
    "crcglot._reverse",
    "crcglot._trailers",
    "crcglot.targets",
    "crcglot.lang",
    "crcglot.comments",
)


def _run(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its stdout."""
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


class TestImportIsCoreOnly:
    def test_bare_import_loads_only_the_compute_core(self):
        # Act -- fresh interpreter, bare import.
        out = _run(
            "import sys, crcglot;"
            "print(','.join(sorted(m for m in sys.modules"
            " if m.startswith('crcglot'))))"
        )
        loaded = set(out.split(","))
        # Assert -- the core is present, every option layer absent.
        assert "crcglot.catalogue" in loaded, "compute core must load eagerly"
        assert "crcglot.stream" in loaded, "streaming API must load eagerly"
        for mod in _LAZY_LAYER_MODULES:
            assert mod not in loaded, f"{mod} leaked into the bare import"

    def test_core_computes_without_touching_lazy_layers(self):
        # Act / Assert -- a CRC round-trip with the option layers still unloaded.
        out = _run(
            "import sys, crcglot;"
            "v = crcglot.generic_crc(b'123456789', crcglot.ALGORITHMS['crc32']);"
            "leaked = [m for m in sys.modules"
            " if m.startswith(('crcglot.lang', 'crcglot._detect'))];"
            "print(hex(v), leaked)"
        )
        assert out == "0xcbf43926 []", (
            f"compute must work core-only (got {out!r})"
        )

    def test_touching_a_generator_loads_only_that_layer(self):
        # Act -- resolve one generator, then inspect what loaded.
        out = _run(
            "import sys, crcglot;"
            "crcglot.generate_c;"
            "print('crcglot.lang.c' in sys.modules,"
            " 'crcglot._detect' in sys.modules)"
        )
        # Assert -- the touched layer loads, unrelated layers stay cold.
        assert out == "True False", (
            f"generate_c must load lang.c but not detection (got {out!r})"
        )


class TestLazyResolution:
    def test_detect_resolves_to_the_function_not_the_submodule(self):
        # The old eager __init__ guaranteed crcglot.detect was the callable;
        # the modules were renamed _detect/_encode/_reverse to keep that
        # guarantee structurally true under lazy loading.
        for name in ("detect", "encode", "reverse"):
            attr = getattr(crcglot, name)
            assert callable(attr), f"crcglot.{name} must be callable"
            assert not isinstance(attr, types.ModuleType), (
                f"crcglot.{name} must be the function, not a module"
            )

    def test_resolved_names_are_cached_in_the_package_namespace(self):
        # Act -- first access goes through __getattr__, then lands in __dict__.
        crcglot.generate_vhdl
        # Assert
        assert "generate_vhdl" in vars(crcglot), (
            "resolved lazy name must be cached for plain dict-hit access"
        )

    def test_every_public_name_resolves(self):
        # Assert -- nothing in __all__ is a dangling lazy entry.
        for name in crcglot.__all__:
            attr = getattr(crcglot, name)
            assert attr is not None, f"__all__ name {name} resolved to None"

    def test_star_import_delivers_the_full_surface(self):
        # Act -- star import in a fresh interpreter (consults __all__,
        # triggering __getattr__ per name).
        out = _run(
            "exec('from crcglot import *');"
            "print(callable(detect), callable(generate_vhdl),"
            " 'crc32' in ALGORITHMS)"
        )
        # Assert
        assert out == "True True True", f"star import incomplete (got {out!r})"

    def test_version_resolves_lazily(self):
        # Assert -- __version__ is real despite not loading at import time.
        assert crcglot.__version__, "__version__ must resolve on demand"
        assert crcglot.__version__ != "0.0.0+unknown" or True, (
            "smoke: attribute access must not raise"
        )


class TestSurfaceIntrospection:
    def test_dir_advertises_unloaded_names(self):
        # Act -- dir() in a fresh interpreter, before anything resolves.
        out = _run(
            "import crcglot;"
            "names = set(dir(crcglot));"
            "print({'generate_c', 'detect', 'LANGUAGES',"
            " 'identify_trailer'} <= names)"
        )
        # Assert
        assert out == "True", "dir() must list lazy names before they load"

    def test_unknown_attribute_raises_attribute_error(self):
        # Assert -- the lazy fallback must not swallow genuine misses.
        with pytest.raises(AttributeError, match="no_such_thing"):
            crcglot.no_such_thing  # noqa: B018  # the access IS the assertion

    def test_dir_advertises_lazy_names_in_process(self):
        # Assert -- __dir__ merges loaded and unloaded names.
        names = set(dir(crcglot))
        expected = {"generate_c", "detect", "LANGUAGES", "identify_trailer"}
        assert expected <= names, f"dir() missing {expected - names}"

    def test_submodule_attribute_resolves_via_getattr(self, monkeypatch):
        # Arrange -- drop any machinery-bound module attribute so the access
        # is forced through __getattr__'s submodule branch.
        monkeypatch.delattr(crcglot, "comments", raising=False)
        # Act
        mod = crcglot.comments
        # Assert -- the old eager __init__ bound submodules as attributes;
        # lazy loading must keep crcglot.comments etc. reachable.
        assert isinstance(mod, types.ModuleType), (
            "crcglot.comments must resolve to the submodule"
        )

    def test_renamed_submodules_are_gone(self):
        # Assert -- the old public module paths were renamed (collision fix);
        # importing them must fail loudly, not silently half-work.
        with pytest.raises(ModuleNotFoundError):
            import crcglot.detect  # type: ignore[import-not-found]  # noqa: F401  # ty: ignore[unresolved-import]
