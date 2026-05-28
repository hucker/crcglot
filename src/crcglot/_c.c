/* _c.c -- crcglot C accelerator for the Rocksoft/Williams CRC engine.
 *
 * Builds as a CPython C extension named ``crcglot._c``.  Exposes:
 *
 *   c_generic_crc(data, width, poly, init, refin, refout, xorout) -> int
 *       Runtime-parameterized CRC over any (catalogue or custom)
 *       algorithm.  Bit-identical to ``crcglot.generic_crc(...)`` in
 *       Python.  Internally auto-selects the fastest engine for the
 *       width:
 *         - width 32 / 64 (byte-aligned): slice-by-8 (8 chained table
 *           lookups per 8 input bytes)
 *         - other byte-aligned widths (8, 16, 24, ...): table-driven
 *           (one lookup per byte)
 *         - non-byte-aligned widths: bit-by-bit
 *       Lookup tables are built once per (width, poly, refin) tuple and
 *       cached, so repeated calls for the same algorithm don't rebuild.
 *
 * Streaming object (``CrcStream``) and the batch API (``c_crc_many``)
 * land in follow-up commits on this branch.
 *
 * ---- CPython API notes for the C-extension-curious ----
 *
 * - We target Python's *Stable ABI* (Py_LIMITED_API).  Pin to 3.11
 *   (``0x030B0000``); one wheel per platform then works for every
 *   future CPython 3.x without rebuilds.  3.11 (not 3.9) is the floor
 *   because the buffer protocol (Py_buffer, PyObject_GetBuffer,
 *   PyBuffer_Release, and the "y*" arg format) only entered the
 *   Stable ABI in 3.11.  crcglot's requires-python is >=3.11 anyway,
 *   so this costs nothing.
 *
 * - Functions that build a Python value from C return ``PyObject*``.
 *   Returning ``NULL`` signals an exception was set (via
 *   ``PyErr_SetString``, ``PyErr_Format``, etc.).  The interpreter
 *   propagates it back to the Python caller as a raised exception.
 *
 * - Accepting bytes / bytearray / memoryview uniformly: use the
 *   *buffer protocol* via the "y*" arg format -- gives a contiguous
 *   ``const uint8_t*`` and length for any bytes-like object.  Release
 *   with ``PyBuffer_Release`` (mandatory; not optional).
 *
 * ---- Table cache + thread safety ----
 *
 * The (width, poly, refin) -> tables cache is shared mutable state.
 * It's safe because every mutation happens while the GIL is held:
 * tables are built and the cache is appended-to *before* we release
 * the GIL for the actual CRC computation.  Cache entries are never
 * mutated or freed once created (append-only), so a thread that
 * fetched a table pointer can run with the GIL released while another
 * thread appends a different entry -- the fetched pointer stays valid.
 * If the cache fills (>CACHE_CAP distinct algorithms), further misses
 * build a thread-local table that the caller frees after use; no
 * caching, but correct.
 */

#define Py_LIMITED_API 0x030B0000
#include <Python.h>

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>


/* ------------------------------------------------------------------ */
/* Primitives                                                          */
/* ------------------------------------------------------------------ */

static uint64_t reflect_bits(uint64_t value, int width) {
    uint64_t result = 0;
    for (int i = 0; i < width; i++) {
        result = (result << 1) | (value & 1ULL);
        value >>= 1;
    }
    return result;
}

static uint64_t width_mask(int width) {
    /* width==64 -> (1ULL << 64) is UB; want all-ones mask instead. */
    return (width == 64) ? ~0ULL : ((1ULL << width) - 1ULL);
}

static uint64_t crc_init_state(int width, uint64_t init, int refin) {
    /* Reflected algorithms enter the loop with the reflected init. */
    return refin ? reflect_bits(init, width) : init;
}

static uint64_t crc_finalize(
    uint64_t crc, int width, int refin, int refout, uint64_t xorout
) {
    if (refout != refin) {
        crc = reflect_bits(crc, width);
    }
    return (crc ^ xorout) & width_mask(width);
}


/* ------------------------------------------------------------------ */
/* Table builders (mirror crcglot._helpers._build_table /             */
/* _build_slice8_tables exactly)                                       */
/* ------------------------------------------------------------------ */

static void build_table(uint64_t *t0, int width, uint64_t poly, int refin) {
    uint64_t mask = width_mask(width);
    if (refin) {
        uint64_t ref_poly = reflect_bits(poly, width);
        for (int i = 0; i < 256; i++) {
            uint64_t crc = (uint64_t)i;
            for (int b = 0; b < 8; b++) {
                crc = (crc & 1ULL) ? ((crc >> 1) ^ ref_poly) : (crc >> 1);
            }
            t0[i] = crc & mask;
        }
    } else {
        uint64_t msb = 1ULL << (width - 1);
        for (int i = 0; i < 256; i++) {
            uint64_t crc = (uint64_t)i << (width - 8);
            for (int b = 0; b < 8; b++) {
                crc = (crc & msb) ? ((crc << 1) ^ poly) : (crc << 1);
                crc &= mask;
            }
            t0[i] = crc;
        }
    }
}

static void build_slice8(
    uint64_t tables[][256], int width, uint64_t poly, int refin
) {
    uint64_t mask = width_mask(width);
    build_table(tables[0], width, poly, refin);
    for (int k = 1; k < 8; k++) {
        const uint64_t *prev = tables[k - 1];
        uint64_t *cur = tables[k];
        if (refin) {
            for (int i = 0; i < 256; i++) {
                uint64_t v = prev[i];
                cur[i] = (tables[0][v & 0xFF] ^ (v >> 8)) & mask;
            }
        } else {
            for (int i = 0; i < 256; i++) {
                uint64_t v = prev[i];
                uint64_t top = (v >> (width - 8)) & 0xFF;
                cur[i] = (tables[0][top] ^ ((v << 8) & mask)) & mask;
            }
        }
    }
}


/* ------------------------------------------------------------------ */
/* Engines.  Each takes the pre-loaded state ``crc`` and returns the   */
/* post-update state; init/finalize are applied by the caller.         */
/* ------------------------------------------------------------------ */

static uint64_t engine_bitwise(
    const uint8_t *data, size_t len, int width,
    uint64_t poly, int refin, uint64_t crc
) {
    uint64_t mask = width_mask(width);
    if (refin) {
        uint64_t ref_poly = reflect_bits(poly, width);
        for (size_t i = 0; i < len; i++) {
            crc ^= (uint64_t)data[i];
            for (int b = 0; b < 8; b++) {
                crc = (crc & 1ULL) ? ((crc >> 1) ^ ref_poly) : (crc >> 1);
            }
        }
    } else {
        uint64_t msb = 1ULL << (width - 1);
        for (size_t i = 0; i < len; i++) {
            crc ^= ((uint64_t)data[i]) << (width - 8);
            for (int b = 0; b < 8; b++) {
                crc = (crc & msb) ? ((crc << 1) ^ poly) : (crc << 1);
            }
            crc &= mask;
        }
    }
    return crc;
}

static uint64_t engine_table(
    const uint8_t *data, size_t len, int width,
    const uint64_t *t0, int refin, uint64_t crc
) {
    uint64_t mask = width_mask(width);
    if (refin) {
        for (size_t i = 0; i < len; i++) {
            crc = t0[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
        }
    } else {
        for (size_t i = 0; i < len; i++) {
            crc = t0[((crc >> (width - 8)) ^ data[i]) & 0xFF]
                  ^ ((crc << 8) & mask);
        }
    }
    return crc;
}

/* Slice-by-8 -- only width 32 / 64.  ``t`` is [8][256].  Transcribed
 * from the reveng-verified generated C in crcglot/c.py. */
static uint64_t engine_slice8(
    const uint8_t *data, size_t len, int width,
    int refin, uint64_t (*t)[256], uint64_t crc
) {
    uint64_t mask = width_mask(width);
    if (width == 32 && refin) {
        while (len >= 8) {
            uint64_t b03 = (uint64_t)data[0] | (uint64_t)data[1] << 8
                         | (uint64_t)data[2] << 16 | (uint64_t)data[3] << 24;
            uint64_t b47 = (uint64_t)data[4] | (uint64_t)data[5] << 8
                         | (uint64_t)data[6] << 16 | (uint64_t)data[7] << 24;
            uint64_t x = crc ^ b03;
            crc = t[7][ x         & 0xFF] ^ t[6][(x   >>  8) & 0xFF]
                ^ t[5][(x  >> 16) & 0xFF] ^ t[4][(x   >> 24) & 0xFF]
                ^ t[3][ b47       & 0xFF] ^ t[2][(b47 >>  8) & 0xFF]
                ^ t[1][(b47 >> 16) & 0xFF] ^ t[0][(b47 >> 24) & 0xFF];
            data += 8; len -= 8;
        }
        while (len--) crc = t[0][(crc ^ *data++) & 0xFF] ^ (crc >> 8);
    } else if (width == 32) {
        while (len >= 8) {
            uint64_t b03 = (uint64_t)data[0] << 24 | (uint64_t)data[1] << 16
                         | (uint64_t)data[2] << 8 | (uint64_t)data[3];
            uint64_t b47 = (uint64_t)data[4] << 24 | (uint64_t)data[5] << 16
                         | (uint64_t)data[6] << 8 | (uint64_t)data[7];
            uint64_t x = crc ^ b03;
            crc = t[7][(x   >> 24) & 0xFF] ^ t[6][(x   >> 16) & 0xFF]
                ^ t[5][(x   >>  8) & 0xFF] ^ t[4][ x         & 0xFF]
                ^ t[3][(b47 >> 24) & 0xFF] ^ t[2][(b47 >> 16) & 0xFF]
                ^ t[1][(b47 >>  8) & 0xFF] ^ t[0][ b47       & 0xFF];
            data += 8; len -= 8;
        }
        while (len--) {
            uint64_t top = crc >> 24;
            crc = t[0][(top ^ *data++) & 0xFF] ^ ((crc << 8) & mask);
        }
    } else if (refin) {  /* width == 64, reflected */
        while (len >= 8) {
            uint64_t b = (uint64_t)data[0] | (uint64_t)data[1] << 8
                       | (uint64_t)data[2] << 16 | (uint64_t)data[3] << 24
                       | (uint64_t)data[4] << 32 | (uint64_t)data[5] << 40
                       | (uint64_t)data[6] << 48 | (uint64_t)data[7] << 56;
            uint64_t x = crc ^ b;
            crc = t[7][ x        & 0xFF] ^ t[6][(x >>  8) & 0xFF]
                ^ t[5][(x >> 16) & 0xFF] ^ t[4][(x >> 24) & 0xFF]
                ^ t[3][(x >> 32) & 0xFF] ^ t[2][(x >> 40) & 0xFF]
                ^ t[1][(x >> 48) & 0xFF] ^ t[0][(x >> 56) & 0xFF];
            data += 8; len -= 8;
        }
        while (len--) crc = t[0][(crc ^ *data++) & 0xFF] ^ (crc >> 8);
    } else {  /* width == 64, non-reflected */
        while (len >= 8) {
            uint64_t b = (uint64_t)data[0] << 56 | (uint64_t)data[1] << 48
                       | (uint64_t)data[2] << 40 | (uint64_t)data[3] << 32
                       | (uint64_t)data[4] << 24 | (uint64_t)data[5] << 16
                       | (uint64_t)data[6] << 8 | (uint64_t)data[7];
            uint64_t x = crc ^ b;
            crc = t[7][(x >> 56) & 0xFF] ^ t[6][(x >> 48) & 0xFF]
                ^ t[5][(x >> 40) & 0xFF] ^ t[4][(x >> 32) & 0xFF]
                ^ t[3][(x >> 24) & 0xFF] ^ t[2][(x >> 16) & 0xFF]
                ^ t[1][(x >>  8) & 0xFF] ^ t[0][ x        & 0xFF];
            data += 8; len -= 8;
        }
        while (len--) {
            uint64_t top = crc >> 56;
            crc = t[0][(top ^ *data++) & 0xFF] ^ ((crc << 8) & mask);
        }
    }
    return crc;
}


/* ------------------------------------------------------------------ */
/* Table cache.  Keyed by (width, poly, refin, has_slice8).  Append-   */
/* only; entries never mutated or freed.  All access under the GIL.    */
/* ------------------------------------------------------------------ */

#define CACHE_CAP 64

typedef struct {
    int width;
    uint64_t poly;
    int refin;
    int has_slice8;
    uint64_t (*tables)[256];  /* heap: 1 row (table) or 8 rows (slice8) */
} TableCache;

static TableCache g_cache[CACHE_CAP];
static int g_cache_len = 0;

/* Return the tables for (width, poly, refin), building + caching on
 * miss.  ``need_slice8`` selects 8-table vs 1-table layout.  Sets
 * ``*needs_free`` to 1 iff the returned pointer is a fresh allocation
 * the caller must ``free`` after use (only when the cache is full).
 * Returns NULL on allocation failure (no Python error set; caller
 * must handle). */
static uint64_t (*get_tables(
    int width, uint64_t poly, int refin, int need_slice8, int *needs_free
))[256] {
    for (int i = 0; i < g_cache_len; i++) {
        TableCache *e = &g_cache[i];
        if (e->width == width && e->poly == poly
            && e->refin == refin && e->has_slice8 == need_slice8) {
            *needs_free = 0;
            return e->tables;
        }
    }
    int rows = need_slice8 ? 8 : 1;
    uint64_t (*t)[256] =
        (uint64_t (*)[256])malloc((size_t)rows * 256 * sizeof(uint64_t));
    if (t == NULL) {
        *needs_free = 0;
        return NULL;
    }
    if (need_slice8) {
        build_slice8(t, width, poly, refin);
    } else {
        build_table(t[0], width, poly, refin);
    }
    if (g_cache_len < CACHE_CAP) {
        TableCache *e = &g_cache[g_cache_len++];
        e->width = width;
        e->poly = poly;
        e->refin = refin;
        e->has_slice8 = need_slice8;
        e->tables = t;
        *needs_free = 0;  /* owned by the cache now */
    } else {
        *needs_free = 1;  /* caller frees after use */
    }
    return t;
}


/* ------------------------------------------------------------------ */
/* Dispatch: pick the engine by width, run init -> engine -> finalize. */
/* ------------------------------------------------------------------ */

/* Buffers >= this release the GIL around the compute (the lock/unlock
 * cost dominates for tiny inputs). */
#define GIL_RELEASE_THRESHOLD 65536

static int crcglot_crc_dispatch(
    const uint8_t *data, size_t len,
    int width, uint64_t poly, uint64_t init,
    int refin, int refout, uint64_t xorout,
    uint64_t *out
) {
    uint64_t crc = crc_init_state(width, init, refin);
    int byte_aligned = (width % 8) == 0;
    int use_slice8 = byte_aligned && (width == 32 || width == 64);
    int use_table = byte_aligned && !use_slice8;
    int big = len >= GIL_RELEASE_THRESHOLD;

    if (use_slice8 || use_table) {
        int needs_free = 0;
        uint64_t (*t)[256] = get_tables(width, poly, refin, use_slice8,
                                        &needs_free);
        if (t == NULL) {
            return -1;  /* OOM */
        }
        if (big) {
            Py_BEGIN_ALLOW_THREADS
            crc = use_slice8
                ? engine_slice8(data, len, width, refin, t, crc)
                : engine_table(data, len, width, t[0], refin, crc);
            Py_END_ALLOW_THREADS
        } else {
            crc = use_slice8
                ? engine_slice8(data, len, width, refin, t, crc)
                : engine_table(data, len, width, t[0], refin, crc);
        }
        if (needs_free) {
            free(t);
        }
    } else {
        /* non-byte-aligned width: bit-by-bit */
        if (big) {
            Py_BEGIN_ALLOW_THREADS
            crc = engine_bitwise(data, len, width, poly, refin, crc);
            Py_END_ALLOW_THREADS
        } else {
            crc = engine_bitwise(data, len, width, poly, refin, crc);
        }
    }

    *out = crc_finalize(crc, width, refin, refout, xorout);
    return 0;
}


/* ------------------------------------------------------------------ */
/* Python wrapper for c_generic_crc                                    */
/* ------------------------------------------------------------------ */

PyDoc_STRVAR(c_generic_crc_doc,
"c_generic_crc(data, width, poly, init, refin, refout, xorout, /) -> int\n"
"\n"
"Compute CRC using Rocksoft/Williams parameterization.\n"
"\n"
"Equivalent to ``crcglot.generic_crc(...)`` -- same algorithm, same\n"
"parameter conventions, same output for every reveng catalogue entry.\n"
"Auto-selects slice-by-8 (width 32/64), table-driven (other\n"
"byte-aligned widths), or bit-by-bit (non-byte-aligned widths) and\n"
"caches lookup tables per (width, poly, refin).\n"
"\n"
"Args:\n"
"    data: bytes-like (bytes, bytearray, memoryview).\n"
"    width: CRC bit width (8-64).\n"
"    poly: generator polynomial in normal (MSB-first) form.\n"
"    init: initial register value.\n"
"    refin: bool, reflect each input byte.\n"
"    refout: bool, reflect the final CRC value.\n"
"    xorout: XOR applied to the final CRC value.\n"
"\n"
"Returns:\n"
"    int with the low `width` bits being the CRC value.\n"
"\n"
"Raises:\n"
"    ValueError: if width is not in [8, 64].\n"
"    MemoryError: if a lookup table allocation fails.\n");

static PyObject *
py_c_generic_crc(PyObject *self, PyObject *args)
{
    (void)self;

    Py_buffer view;
    int width;
    unsigned long long poly, init, xorout;
    int refin, refout;

    if (!PyArg_ParseTuple(args, "y*iKKppK",
                          &view, &width, &poly, &init,
                          &refin, &refout, &xorout)) {
        return NULL;
    }

    if (width < 8 || width > 64) {
        PyBuffer_Release(&view);
        PyErr_Format(PyExc_ValueError,
                     "width must be in [8, 64], got %d", width);
        return NULL;
    }

    uint64_t result = 0;
    int rc = crcglot_crc_dispatch(
        (const uint8_t *)view.buf, (size_t)view.len,
        width, poly, init, refin, refout, xorout, &result);

    PyBuffer_Release(&view);

    if (rc != 0) {
        return PyErr_NoMemory();
    }
    return PyLong_FromUnsignedLongLong((unsigned long long)result);
}


/* ------------------------------------------------------------------ */
/* Module definition                                                   */
/* ------------------------------------------------------------------ */

static PyMethodDef crcglot_c_methods[] = {
    {"c_generic_crc", py_c_generic_crc, METH_VARARGS, c_generic_crc_doc},
    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(module_doc,
"crcglot._c -- C accelerator for crcglot.\n"
"\n"
"C-backed Rocksoft/Williams CRC engine that ``crcglot.generic_crc``\n"
"dispatches to when available.  Auto-selects slice-by-8 / table-driven\n"
"/ bit-by-bit by width and caches tables per algorithm.  Optional; the\n"
"pure-Python fallback in crcglot.catalogue is always present.\n"
"\n"
"Installed by the ``crcglot[fast]`` extra / the prebuilt wheel.\n");

static struct PyModuleDef crcglot_c_module = {
    PyModuleDef_HEAD_INIT,
    "crcglot._c",
    module_doc,
    -1,
    crcglot_c_methods,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC
PyInit__c(void)
{
    return PyModule_Create(&crcglot_c_module);
}
