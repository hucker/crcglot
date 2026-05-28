/* _c.c -- crcglot C accelerator for the Rocksoft/Williams CRC engine.
 *
 * Builds as a CPython C extension named ``crcglot._c``.  Exposes:
 *
 *   c_generic_crc(data, width, poly, init, refin, refout, xorout) -> int
 *       Runtime-parameterized CRC over any (catalogue or custom) algorithm.
 *       Bit-identical to ``crcglot.generic_crc(...)`` in Python, ~50-200x
 *       faster on small buffers thanks to a tight C loop.
 *
 * Streaming object (``CrcStream``) and the batch API (``c_crc_many``)
 * land in follow-up commits on this branch.  Single-shot first to keep
 * the wheel-build setup verifiable.
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
 * - Reference counting: every PyObject* you create from a Py_* call
 *   you own a reference to and must eventually release with ``Py_DECREF``
 *   (or transfer ownership by returning it from a function).  Borrowed
 *   refs (e.g. from PyDict_GetItem) you must NOT DECREF.  For this
 *   extension we mostly consume objects (args) and create one (the
 *   return), so refcount discipline is light.
 *
 * - Accepting bytes / bytearray / memoryview uniformly: use the
 *   *buffer protocol* via ``PyObject_GetBuffer`` -- gives a contiguous
 *   ``const uint8_t*`` and length for any bytes-like object.  Release
 *   with ``PyBuffer_Release`` (mandatory; not optional).
 */

#define Py_LIMITED_API 0x030B0000
#include <Python.h>

#include <stdint.h>
#include <stddef.h>


/* -- The engine: pure C, no Python types.  Mirrors generic_crc in
 *    crcglot/catalogue.py exactly; identical results for any input. */

static uint64_t reflect_bits(uint64_t value, int width) {
    uint64_t result = 0;
    for (int i = 0; i < width; i++) {
        result = (result << 1) | (value & 1ULL);
        value >>= 1;
    }
    return result;
}

static uint64_t crcglot_generic_crc(
    const uint8_t *data, size_t len,
    int width, uint64_t poly, uint64_t init,
    int refin, int refout, uint64_t xorout
) {
    /* width==64 -> (1ULL << 64) is UB; want all-ones mask instead. */
    uint64_t mask = (width == 64) ? ~0ULL : ((1ULL << width) - 1ULL);
    uint64_t crc;
    if (refin) {
        uint64_t ref_poly = reflect_bits(poly, width);
        crc = reflect_bits(init, width);
        for (size_t i = 0; i < len; i++) {
            crc ^= (uint64_t)data[i];
            for (int b = 0; b < 8; b++) {
                crc = (crc & 1ULL) ? ((crc >> 1) ^ ref_poly) : (crc >> 1);
            }
        }
    } else {
        uint64_t msb_mask = 1ULL << (width - 1);
        crc = init;
        for (size_t i = 0; i < len; i++) {
            crc ^= ((uint64_t)data[i]) << (width - 8);
            for (int b = 0; b < 8; b++) {
                crc = (crc & msb_mask) ? ((crc << 1) ^ poly) : (crc << 1);
            }
            crc &= mask;
        }
    }
    if (refout != refin) {
        crc = reflect_bits(crc, width);
    }
    return (crc ^ xorout) & mask;
}


/* -- Python wrapper for c_generic_crc -- */

PyDoc_STRVAR(c_generic_crc_doc,
"c_generic_crc(data, width, poly, init, refin, refout, xorout, /) -> int\n"
"\n"
"Compute CRC using Rocksoft/Williams parameterization.\n"
"\n"
"Equivalent to ``crcglot.generic_crc(...)`` -- same algorithm, same\n"
"parameter conventions, same output for every reveng catalogue entry.\n"
"This is the C-backed fast path; callers should prefer the Python\n"
"``generic_crc`` (which transparently dispatches here when available).\n"
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
"    int with the low `width` bits being the CRC value.\n");

static PyObject *
py_c_generic_crc(PyObject *self, PyObject *args)
{
    (void)self;

    Py_buffer view;
    int width;
    unsigned long long poly, init, xorout;
    int refin, refout;

    /* Format string: y* = buffer-protocol bytes-like (releases via
     *                     PyBuffer_Release; works on bytes, bytearray,
     *                     memoryview, array.array, etc.)
     *                i = int (for width)
     *                K = unsigned long long (for poly, init, xorout)
     *                p = bool-ish int (for refin, refout) */
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

    /* Run the engine.  Release the GIL for buffers big enough to be
     * worth it -- for tiny buffers the lock/unlock cost dominates the
     * computation.  64 KiB is a rough breakeven heuristic. */
    uint64_t result;
    if (view.len >= 65536) {
        Py_BEGIN_ALLOW_THREADS
        result = crcglot_generic_crc(
            (const uint8_t *)view.buf, (size_t)view.len,
            width, poly, init, refin, refout, xorout);
        Py_END_ALLOW_THREADS
    } else {
        result = crcglot_generic_crc(
            (const uint8_t *)view.buf, (size_t)view.len,
            width, poly, init, refin, refout, xorout);
    }

    PyBuffer_Release(&view);
    return PyLong_FromUnsignedLongLong((unsigned long long)result);
}


/* -- Module method table -- */

static PyMethodDef crcglot_c_methods[] = {
    {"c_generic_crc", py_c_generic_crc, METH_VARARGS, c_generic_crc_doc},
    {NULL, NULL, 0, NULL}  /* sentinel */
};


/* -- Module definition + init function -- */

PyDoc_STRVAR(module_doc,
"crcglot._c -- C accelerator for crcglot.\n"
"\n"
"Exposes a C-backed implementation of the Rocksoft/Williams CRC engine\n"
"that ``crcglot.generic_crc`` dispatches to when available.  Optional;\n"
"the pure-Python fallback in crcglot.catalogue is always present.\n"
"\n"
"This module is installed by the ``crcglot[fast]`` extra.\n");

static struct PyModuleDef crcglot_c_module = {
    PyModuleDef_HEAD_INIT,
    "crcglot._c",         /* m_name */
    module_doc,           /* m_doc */
    -1,                   /* m_size: no per-module state */
    crcglot_c_methods,    /* m_methods */
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC
PyInit__c(void)
{
    return PyModule_Create(&crcglot_c_module);
}
