"""The one place crcglot's version is written.

``scripts/release_prep.py`` bumps this line; setuptools reads the attribute
statically at build time (``[tool.setuptools.dynamic]`` in pyproject.toml)
and ``crcglot.__version__`` resolves to it, so the package, the wheel and
``crcglot version`` cannot disagree.  Reading it costs one small module,
where ``importlib.metadata`` scanned every installed distribution.
"""

__version__ = "0.33.1"
