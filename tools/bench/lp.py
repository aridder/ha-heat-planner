"""A small dense LP solver (Mehrotra predictor–corrector interior point), numpy only.

    minimise c·x  subject to  A x ≤ b,  lb ≤ x ≤ ub

Used for the EMHASS-style thermal optimisation (EMHASS itself builds the same
LP with cvxpy/HiGHS); problems here have ≤ 96 variables and ≤ 96 general rows.
Bounds are handled as diagonal terms, so a solve is a few milliseconds.
"""

from __future__ import annotations

import numpy as np


def solve(c: np.ndarray, A: np.ndarray, b: np.ndarray, lb: np.ndarray, ub: np.ndarray,
          *, tol: float = 1e-8, iters: int = 50) -> np.ndarray:
    m, n = A.shape
    x = (lb + ub) / 2  # bounds must be finite
    # slacks and duals for the three row blocks: A x ≤ b, x ≤ ub, −x ≤ −lb
    sA = np.maximum(b - A @ x, 1.0)
    su = np.maximum(ub - x, 1.0)
    sl = np.maximum(x - lb, 1.0)
    zA, zu, zl = np.ones(m), np.ones(n), np.ones(n)
    total = m + 2 * n
    scale = 1.0 + max(np.abs(c).max(), np.abs(b).max(initial=0.0))
    for _ in range(iters):
        rd = A.T @ zA + zu - zl + c
        rA = A @ x + sA - b
        ru = x + su - ub
        rl = -x + sl + lb
        mu = (sA @ zA + su @ zu + sl @ zl) / total
        if mu < tol * scale and max(np.abs(rA).max(initial=0), np.abs(ru).max(), np.abs(rl).max()) < 1e-7 * scale \
                and np.abs(rd).max() < 1e-7 * scale:
            break
        dA, du, dl = zA / sA, zu / su, zl / sl
        K = A.T @ (A * dA[:, None])
        K[np.diag_indices(n)] += du + dl + 1e-12
        Kinv = np.linalg.inv(K)  # one factorisation per iteration, used by both Newton solves

        def newton(cA, cu, cl):
            qA, qu, ql = (cA - zA * rA) / sA, (cu - zu * ru) / su, (cl - zl * rl) / sl
            rhs = -rd + A.T @ qA + qu - ql
            dx = Kinv @ rhs
            gA = A @ dx
            dzA = (-cA + zA * rA + zA * gA) / sA
            dzu = (-cu + zu * ru + zu * dx) / su
            dzl = (-cl + zl * rl - zl * dx) / sl
            return dx, -rA - gA, -ru - dx, -rl + dx, dzA, dzu, dzl

        def step(v, dv):
            neg = dv < 0
            return min(1.0, float(np.min(-v[neg] / dv[neg]))) if neg.any() else 1.0

        def alpha(d):
            _, dsA, dsu, dsl, dzA, dzu, dzl = d
            return min(step(sA, dsA), step(su, dsu), step(sl, dsl), step(zA, dzA), step(zu, dzu), step(zl, dzl))

        aff = newton(sA * zA, su * zu, sl * zl)
        a = alpha(aff)
        _, dsA, dsu, dsl, dzA, dzu, dzl = aff
        mu_aff = ((sA + a * dsA) @ (zA + a * dzA) + (su + a * dsu) @ (zu + a * dzu)
                  + (sl + a * dsl) @ (zl + a * dzl)) / total
        sigma = (mu_aff / mu) ** 3
        d = newton(sA * zA + dsA * dzA - sigma * mu, su * zu + dsu * dzu - sigma * mu,
                   sl * zl + dsl * dzl - sigma * mu)
        a = 0.99 * alpha(d)
        dx, dsA, dsu, dsl, dzA, dzu, dzl = d
        x = x + a * dx
        sA, su, sl = sA + a * dsA, su + a * dsu, sl + a * dsl
        zA, zu, zl = zA + a * dzA, zu + a * dzu, zl + a * dzl
    return x
