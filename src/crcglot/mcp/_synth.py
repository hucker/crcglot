"""Synthesize MCP tool callables from the verb manifest.

The mcp 2.0 ``MCPServer.add_tool`` builds a tool's ``inputSchema`` by
introspecting the registered callable's signature.  Rather than keep
twelve hand-written wrapper functions whose parameter lists duplicate
:data:`crcglot.VERBS` (the 1.x shape, held together by a drift test),
this module builds each callable *from* the manifest: the
:class:`~crcglot.verbs.ParamSpec` rows become ``inspect.Parameter``
entries with annotations mapped from the manifest's closed type
vocabulary, choices become ``Literal[...]`` enums, and the body is a
one-line dispatch to the matching ``crcglot._invoke._verb_*``
implementation.

The wire schema is therefore derived from the manifest by
construction; ``tests/goldens/mcp_wire.json`` (captured from the last
mcp 1.x build) pins the derivation to the previously shipped shapes so
the port provably did not move the wire.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Literal

from crcglot import _invoke
from crcglot.verbs import VERBS, ParamSpec

#: Verbs whose implementation takes a ``surface=`` keyword for the
#: where-to-look-next hint in error messages ("crc_list" on this surface,
#: ``crcglot.ALGORITHMS`` on the Python surface).
_SURFACE_VERBS = frozenset(
    {"info", "vectors", "verify", "compute", "compute_many", "encode", "generate"}
)

#: The manifest's closed type vocabulary -> Python annotation.
_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "object": dict[str, Any],
    "array[string]": list[str],
    "string | array[string]": str | list[str],
}


def _annotation(p: ParamSpec) -> Any:
    """Map one ParamSpec to the annotation the schema builder should see.

    Choices become a ``Literal`` enum (the manifest only puts choices on
    string parameters).  A parameter that is optional *and* defaults to
    ``None`` gets ``| None`` so the schema allows null, matching the
    1.x wire shape (``anyOf: [<type>, null]``).
    """
    if p.choices:
        # Runtime-constructed Literal: inherently untypeable (checkers
        # require literal arguments), deliberate here -- the enum comes
        # from the manifest at import time.
        base: Any = Literal[tuple(c.name for c in p.choices)]  # type: ignore[valid-type]  # ty: ignore[invalid-type-form]
    else:
        base = _TYPE_MAP[p.type]
    if not p.required and p.default is None:
        return base | None
    return base


def synthesize_tool(verb: str) -> Callable[..., dict[str, Any]]:
    """Build the callable for ``verb`` from its manifest entry.

    The returned function carries a synthesized ``__signature__`` and
    ``__annotations__`` (what ``add_tool`` introspects) and dispatches
    to ``_invoke._verb_<verb>``, adding ``surface="mcp"`` where the
    implementation offers the hint.

    Args:
        verb: A key of :data:`crcglot.VERBS`.

    Returns:
        A keyword-only callable returning the verb's wire dict.
    """
    spec = VERBS[verb]
    impl = getattr(_invoke, f"_verb_{verb}")
    pass_surface = verb in _SURFACE_VERBS

    params = []
    annotations: dict[str, Any] = {}
    for p in spec.params:
        ann = _annotation(p)
        default = inspect.Parameter.empty if p.required else p.default
        params.append(
            inspect.Parameter(
                p.name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=ann,
                default=default,
            )
        )
        annotations[p.name] = ann

    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        if pass_surface:
            kwargs["surface"] = "mcp"
        return impl(**kwargs)

    tool_fn.__name__ = spec.mcp_tool
    tool_fn.__qualname__ = spec.mcp_tool
    tool_fn.__doc__ = spec.summary
    tool_fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        params, return_annotation=dict[str, Any]
    )
    annotations["return"] = dict[str, Any]
    tool_fn.__annotations__ = annotations
    return tool_fn
