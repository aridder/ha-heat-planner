"""Heating-strategy benchmark on the digital twin (``python -m tools.bench``)."""

import os

# many small LPs in several processes: multi-threaded BLAS only fights itself (4× slower)
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
