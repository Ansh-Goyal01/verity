"""Bayesian calibration of per-vehicle physics parameters, with hierarchical fleet pooling.

Method (also in docs/DECISIONS.md): Laplace approximation.

* Unknowns: ``u`` (6 values, see ``vehicles.THETA_NAMES``), prior N(mu0, Sigma0) from the vehicle class.
* Data: for each logged trip, the SoC drop between the first and last reading, ``y`` (whole-percent
  readings, so rounding noise has variance 1/6 pp^2) versus the physics prediction ``f(u)``.
* Likelihood: y ~ N(f(u), sigma^2) with sigma^2 = 1/6 + (rho_rel * y)^2. ``rho_rel`` is a relative
  model-discrepancy allowance (planner physics vs reality) fixed from the DEV simulator seed.
* MAP by damped Gauss-Newton (Levenberg-Marquardt) on the whitened residuals, with a batched
  finite-difference Jacobian (one vectorised physics call evaluates all 7 perturbed parameter vectors).
* Posterior ~ N(u_MAP, (J^T J)^-1), where J includes the prior rows, i.e. H = J_data^T J_data + Sigma0^-1.

Why Laplace and not MCMC: the model is smooth and the posterior near-Gaussian in the unconstrained
space; it costs ~0.1 s per fit, so fleet-wide experiments and the live API stay fast, and the
posterior covariance is explicit and inspectable. Its weakness is skewed posteriors with 1-2 trips.

Fleet pooling is hierarchical empirical Bayes (model-level prior -> vehicle-level posterior):
``pool_fleet`` learns the class-level mean and spread in the directions the data can identify and
leaves the rest at the original class prior.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .physics import J_PER_KWH, VehicleParams, energy_terms, total_energy_j
from .trips import TripObservation
from .vehicles import N_THETA, THETA_NAMES, to_params, to_physical

Array = np.ndarray

SIGMA_Q2 = 1.0 / 6.0  # variance of the difference of two whole-percent roundings
DEFAULT_RHO_REL = 0.06  # relative model discrepancy; chosen on the DEV dataset only
FD_EPS = 1e-4


def predict_delta_soc(
    base: VehicleParams, nominal_kwh: float, u: Array, obs: TripObservation, *, aux_scale: float = 1.0
) -> Array:
    """Predicted SoC drop (pp) over the observation's SoC window for each row of ``u`` -> shape (N,)."""
    u2 = np.atleast_2d(np.asarray(u, dtype=float))
    params = to_params(base, nominal_kwh, u2)
    terms = energy_terms(params, obs.segments, mass_kg=obs.mass_kg, ac_on=obs.ac_on, aux_scale=aux_scale)
    e_window = (total_energy_j(terms, axis=None) * obs.window_weights).sum(axis=-1)
    return 100.0 * e_window / (params.usable_kwh[:, 0] * J_PER_KWH)


@dataclass(frozen=True, eq=False)
class Posterior:
    """Gaussian posterior of ``u`` for one vehicle (or the prior itself when n_trips == 0)."""

    mean_u: Array
    cov_u: Array
    n_trips: int
    rho_rel: float
    base: VehicleParams
    nominal_kwh: float

    def sample_u(self, rng: np.random.Generator, n: int) -> Array:
        return rng.multivariate_normal(self.mean_u, self.cov_u, size=n)

    def summary(self, n: int = 4000, seed: int = 0) -> dict[str, tuple[float, float]]:
        """(mean, sd) of each physical parameter under the posterior."""
        phys = to_physical(self.sample_u(np.random.default_rng(seed), n))
        return {name: (float(phys[name].mean()), float(phys[name].std())) for name in THETA_NAMES}

    def usable_kwh_quantiles(self, qs: tuple[float, ...] = (0.1, 0.5, 0.9), n: int = 4000, seed: int = 0) -> Array:
        phys = to_physical(self.sample_u(np.random.default_rng(seed), n))
        return np.quantile(self.nominal_kwh * phys["usable_frac"], qs)


def calibrate(
    prior_mean: Array,
    prior_cov: Array,
    observations: list[TripObservation],
    base: VehicleParams,
    nominal_kwh: float,
    rho_rel: float = DEFAULT_RHO_REL,
    max_iter: int = 40,
) -> Posterior:
    """Laplace-approximate posterior of ``u`` given the observed SoC drops of ``observations``."""
    prior_mean = np.asarray(prior_mean, dtype=float)
    prior_cov = np.asarray(prior_cov, dtype=float)
    if not observations:
        return Posterior(prior_mean.copy(), prior_cov.copy(), 0, rho_rel, base, nominal_kwh)

    linv = np.linalg.inv(np.linalg.cholesky(prior_cov))
    y = np.array([o.delta_soc_pct for o in observations])
    sigma = np.sqrt(SIGMA_Q2 + (rho_rel * y) ** 2)

    def evaluate(u: Array) -> tuple[Array, Array, float]:
        rows = np.vstack([u, u + FD_EPS * np.eye(N_THETA)])
        preds = np.stack([predict_delta_soc(base, nominal_kwh, rows, o) for o in observations], axis=1)
        jac_data = ((preds[1:] - preds[0]) / FD_EPS).T  # (n, 6)
        resid = np.concatenate([(y - preds[0]) / sigma, linv @ (u - prior_mean)])
        jac = np.vstack([-jac_data / sigma[:, None], linv])
        return resid, jac, 0.5 * float(resid @ resid)

    u = prior_mean.copy()
    resid, jac, cost = evaluate(u)
    lam = 1e-2
    for _ in range(max_iter):
        hess = jac.T @ jac
        step = np.linalg.solve(hess + lam * np.diag(np.diag(hess)), -jac.T @ resid)
        resid_n, jac_n, cost_n = evaluate(u + step)
        if cost_n < cost:
            converged = abs(cost - cost_n) < 1e-9 * (1.0 + cost)
            u, resid, jac, cost = u + step, resid_n, jac_n, cost_n
            lam = max(lam / 3.0, 1e-9)
            if converged:
                break
        else:
            lam *= 5.0
            if lam > 1e8:
                break
    cov = np.linalg.inv(jac.T @ jac)
    return Posterior(u, 0.5 * (cov + cov.T), len(observations), rho_rel, base, nominal_kwh)


def predictive_samples(
    post: Posterior, obs: TripObservation, rng: np.random.Generator, n: int = 400, *, add_noise: bool = True
) -> Array:
    """Posterior-predictive samples of the observed SoC drop (pp): parameter draws + discrepancy + rounding."""
    pred = predict_delta_soc(post.base, post.nominal_kwh, post.sample_u(rng, n), obs)
    if not add_noise:
        return pred
    sd = np.sqrt(SIGMA_Q2 + (post.rho_rel * pred) ** 2)
    return pred + rng.normal(0.0, 1.0, size=n) * sd


def central_band(samples: Array, level: float = 0.90) -> tuple[float, float]:
    lo, hi = np.quantile(samples, [(1 - level) / 2, 1 - (1 - level) / 2])
    return float(lo), float(hi)


def pool_fleet(
    posteriors: list[Posterior], prior_mean: Array, prior_cov: Array, min_info: float = 1.0, floor: float = 0.30
) -> tuple[Array, Array]:
    """Empirical-Bayes class-level prior from several vehicles' posteriors (model -> vehicle hierarchy).

    Works in the eigenbasis of the average *data information* (whitened by the class prior). In directions
    the data identify (information >= ``min_info`` prior units) the pooled mean is the fleet mean of the
    un-shrunk estimates and the variance is the between-vehicle spread, floored at ``floor`` x the class
    prior variance; in the other directions the class prior is kept, so poorly identified parameters
    never become falsely certain. The floor is large (0.30) because a spread estimated from a handful
    of vehicles is noisy; 0.05 was overconfident on the DEV dataset (90 % band covered only 81 % at n=1).
    """
    prior_mean = np.asarray(prior_mean, dtype=float)
    prior_cov = np.asarray(prior_cov, dtype=float)
    if len(posteriors) < 2:
        return prior_mean.copy(), prior_cov.copy()
    chol = np.linalg.cholesky(prior_cov)
    linv = np.linalg.inv(chol)
    prior_prec = linv.T @ linv
    info = np.mean([np.linalg.inv(p.cov_u) - prior_prec for p in posteriors], axis=0)
    whitened_info = chol.T @ info @ chol
    evals, q = np.linalg.eigh(0.5 * (whitened_info + whitened_info.T))
    evals = np.clip(evals, 1e-9, None)

    z_hat = np.array([(q.T @ linv @ (p.mean_u - prior_mean)) * (1.0 + evals) / evals for p in posteriors])
    identified = evals >= min_info
    mu_z = np.where(identified, z_hat.mean(axis=0), 0.0)
    between = np.clip(z_hat.var(axis=0, ddof=1) - 1.0 / evals, floor, 1.0)
    var_z = np.where(identified, between, 1.0)
    mean = prior_mean + chol @ q @ mu_z
    cov = chol @ q @ np.diag(var_z) @ q.T @ chol.T
    return mean, 0.5 * (cov + cov.T)
