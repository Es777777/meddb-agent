"""Local, predefined statistical methods. Never execute model-written Python/R."""
from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(x) for x in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            raise ValueError("Non-finite statistical result; inspect model identifiability")
        return float(value)
    return value


def binary(series: pd.Series, name: str):
    if not set(series.dropna().unique()) <= {0, 1}:
        raise ValueError(f"{name} must be encoded as 0/1")


def numeric(frame: pd.DataFrame, columns: list[str]):
    for column in columns:
        if not pd.api.types.is_numeric_dtype(frame[column]) or not np.isfinite(frame[column].dropna()).all():
            raise ValueError("Numeric roles require finite numeric columns")


def prepare(frame: pd.DataFrame, a: dict, roles: list[str]) -> tuple[pd.DataFrame, dict]:
    features = list(dict.fromkeys(([a["exposure"]] if "exposure" in a else []) + a.get("covariates", [])))
    columns = list(dict.fromkeys(roles + features + ([a["cluster_key"]] if "cluster_key" in a else [])))
    data = frame[columns].copy()
    before = len(data)
    # Outcomes, follow-up, event indicators and cluster keys are never imputed.
    data = data.dropna(subset=[c for c in columns if c not in features])
    imputed = 0
    if a.get("missing", "complete_case") == "median":
        for c in features:
            imputed += int(data[c].isna().sum())
            if c in a.get("categorical", []):
                modes = data[c].mode()
                if modes.empty:
                    raise ValueError("Cannot impute an entirely missing feature")
                data[c] = data[c].fillna(modes.iloc[0])
            else:
                numeric(data, [c])
                data[c] = data[c].fillna(data[c].median())
    data = data.dropna()
    if len(data) < 5:
        raise ValueError("Insufficient complete observations")
    return data, {"input_rows": before, "analysis_rows": len(data), "excluded_missing": before - len(data),
                  "imputed_cells": imputed, "missing_strategy": a.get("missing", "complete_case")}


def design_matrix(data: pd.DataFrame, a: dict) -> pd.DataFrame:
    import statsmodels.api as sm
    features = list(dict.fromkeys(([a["exposure"]] if "exposure" in a else []) + a.get("covariates", [])))
    if any(data[c].nunique() > 50 for c in a.get("categorical", [])):
        raise ValueError("Categorical model features are limited to 50 observed levels")
    if any(data[c].nunique() < 2 for c in features):
        raise ValueError("A model feature has no observable contrast")
    numeric(data, [c for c in features if c not in a.get("categorical", [])])
    matrix = pd.get_dummies(data[features], columns=a.get("categorical", []), drop_first=True, dtype=float)
    matrix = sm.add_constant(matrix.astype(float), has_constant="add")
    if len(matrix) <= matrix.shape[1] + 1 or np.linalg.matrix_rank(matrix) < matrix.shape[1]:
        raise ValueError("Model design is rank deficient or lacks residual degrees of freedom")
    return matrix


def describe(frame: pd.DataFrame, a: dict) -> dict:
    data, flow = prepare(frame, a, [a["outcome"]] + ([a["group"]] if "group" in a else []))
    numeric(data, [a["outcome"]])
    groups = data.groupby(a["group"], observed=True) if "group" in a else [("all", data)]
    if "group" in a and data[a["group"]].nunique() > 20:
        raise ValueError("At most 20 reporting groups are supported")
    records = []
    for label, subset in groups:
        y = subset[a["outcome"]]
        records.append({"group": str(label), "n": len(y), "mean": y.mean(), "sd": y.std(ddof=1),
                        "median": y.median(), "q1": y.quantile(.25), "q3": y.quantile(.75)})
    return {"method": "describe", "flow": flow, "groups": records,
            "limitations": ["Sample description; no population or causal inference"]}


def regression(frame: pd.DataFrame, a: dict) -> dict:
    import statsmodels.api as sm
    data, flow = prepare(frame, a, [a["outcome"]])
    numeric(data, [a["outcome"]])
    x, y = design_matrix(data, a), data[a["outcome"]]
    logistic = a["method"] == "logistic"
    if logistic:
        binary(y, "outcome")
        if y.nunique() != 2:
            raise ValueError("Logistic regression requires both outcome classes")
        model = sm.GLM(y, x, family=sm.families.Binomial())
    else:
        model = sm.OLS(y, x)
    if "cluster_key" in a:
        if data[a["cluster_key"]].nunique() < 3:
            raise ValueError("Cluster-robust inference requires at least three clusters")
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": data[a["cluster_key"]]}, use_t=True)
    else:
        fit = model.fit(cov_type="HC3")
    if not getattr(fit, "converged", True):
        raise ValueError("Regression did not converge")
    ci = fit.conf_int(alpha=1-a.get("confidence", .95))
    coefficients = []
    for name in x.columns:
        row = {"term": name, "estimate": fit.params[name], "se": fit.bse[name],
               "lower": ci.loc[name, 0], "upper": ci.loc[name, 1], "p_value": fit.pvalues[name]}
        if logistic:
            row.update(odds_ratio=np.exp(row["estimate"]), or_lower=np.exp(row["lower"]), or_upper=np.exp(row["upper"]))
        coefficients.append(row)
    return {"method": a["method"], "flow": flow, "coefficients": coefficients,
            "confidence": a.get("confidence", .95), "converged": True,
            "limitations": ["Adjusted observational association is not a causal effect",
                "Single imputation does not account for imputation uncertainty"] if flow["imputed_cells"] else
                ["Adjusted observational association is not a causal effect"]}


def survival(frame: pd.DataFrame, a: dict) -> dict:
    from lifelines import AalenJohansenFitter, CoxPHFitter, KaplanMeierFitter
    roles = [a["duration"], a["event"]] + ([a["group"]] if "group" in a else [])
    data, flow = prepare(frame, a, roles)
    numeric(data, [a["duration"], a["event"]])
    if (data[a["duration"]] < 0).any():
        raise ValueError("Follow-up durations must be nonnegative")
    method = a["method"]
    alpha = 1-a.get("confidence", .95)
    if method == "cox":
        binary(data[a["event"]], "event")
        if data[a["event"]].sum() < 5:
            raise ValueError("Cox regression requires at least five observed events")
        x = design_matrix(data, a).drop(columns="const")
        fit_data = x.copy()
        fit_data["_time"] = data[a["duration"]].to_numpy()
        fit_data["_event"] = data[a["event"]].to_numpy()
        kwargs = {}
        if "cluster_key" in a:
            if data[a["cluster_key"]].nunique() < 3:
                raise ValueError("Cox clustering requires at least three clusters")
            fit_data["_cluster"] = data[a["cluster_key"]].to_numpy()
            kwargs["cluster_col"] = "_cluster"
        fit = CoxPHFitter(alpha=alpha).fit(fit_data, "_time", "_event", robust=True, **kwargs)
        coefficients = [{"term": name, "estimate": row["coef"], "se": row["se(coef)"],
            "lower": float(fit.confidence_intervals_.loc[name].iloc[0]),
            "upper": float(fit.confidence_intervals_.loc[name].iloc[1]), "hazard_ratio": row["exp(coef)"],
            "p_value": row["p"]} for name, row in fit.summary.iterrows()]
        from lifelines.statistics import proportional_hazard_test
        # lifelines retains cluster keys for its own residual calculation.
        ph = proportional_hazard_test(fit, fit_data, time_transform="rank")
        return {"method": method, "flow": flow, "coefficients": coefficients,
            "events": int(data[a["event"]].sum()), "ph_test_p_values": ph.summary["p"].to_dict(),
            "concordance": fit.concordance_index_,
            "limitations": ["Proportional hazards and non-informative censoring require review",
                "Tied-event robust variance approximations may affect inference"]}
    if method == "kaplan_meier":
        binary(data[a["event"]], "event")
    elif not set(data[a["event"]].unique()) <= {0, 1, 2}:
        raise ValueError("Competing risk coding: 0=censored, 1=target, 2=competing event")
    groups = data.groupby(a["group"], observed=True) if "group" in a else [("all", data)]
    if "group" in a and data[a["group"]].nunique() > 20:
        raise ValueError("At most 20 survival groups are supported")
    curves = []
    for label, subset in groups:
        if len(subset) < 5:
            raise ValueError("Survival groups need at least five observations")
        if method == "competing_risk":
            fit = AalenJohansenFitter(alpha=alpha, seed=a.get("seed", 2026)).fit(
                subset[a["duration"]], subset[a["event"]], event_of_interest=1)
            curve = fit.cumulative_density_
        else:
            fit = KaplanMeierFitter(alpha=alpha).fit(subset[a["duration"]], subset[a["event"]])
            curve = fit.survival_function_
        ci = fit.confidence_interval_.reindex(curve.index)
        # Some lifelines AJ boundary intervals are undefined; curve estimates
        # remain valid and those boundaries are explicitly reported as null.
        curves.append({"group": str(label), "n": len(subset),
            "time": curve.index.tolist(), "estimate": curve.iloc[:, 0].tolist(),
            "lower": [None if pd.isna(v) else float(v) for v in ci.min(axis=1)],
            "upper": [None if pd.isna(v) else float(v) for v in ci.max(axis=1)]})
    return {"method": method, "flow": flow, "curves": curves,
            "limitations": ["Independent censoring assumption requires review"] +
                (["Aalen-Johansen target event=1; competing event=2; tied times may be jittered deterministically"]
                 if method == "competing_risk" else [])}


def survey_meat(scores: np.ndarray, frame: pd.DataFrame, a: dict) -> tuple[np.ndarray, int]:
    score_frame = pd.DataFrame(scores, index=frame.index)
    score_frame["_strata"] = frame[a["strata"]].to_numpy()
    score_frame["_psu"] = frame[a["psu"]].to_numpy()
    totals = score_frame.groupby(["_strata", "_psu"], observed=True).sum()
    meat = np.zeros((scores.shape[1], scores.shape[1]))
    df = 0
    for _, part in totals.groupby(level=0):
        m = len(part)
        if m < 2:
            raise ValueError("Survey strata need at least two PSUs; singleton policy must be resolved explicitly")
        centered = part.to_numpy() - part.to_numpy().mean(axis=0)
        meat += m/(m-1) * centered.T @ centered
        df += m-1
    if df < 2:
        raise ValueError("Insufficient survey design degrees of freedom")
    return meat, df


def survey(frame: pd.DataFrame, a: dict) -> dict:
    import statsmodels.api as sm
    for c in (a["weight"], a["strata"], a["psu"]):
        if frame[c].isna().any():
            raise ValueError("Survey design variables may not be missing")
    numeric(frame, [a["weight"], a["outcome"]])
    if (frame[a["weight"]] <= 0).any():
        raise ValueError("Survey weights must be positive")
    valid = frame.get("_domain", pd.Series(True, index=frame.index)).astype(bool)
    roles = [a["outcome"]] + ([a["exposure"]] if "exposure" in a else []) + a.get("covariates", [])
    valid &= frame[roles].notna().all(axis=1)
    data = frame.loc[valid]
    if len(data) < 5:
        raise ValueError("Insufficient observations in the survey analysis domain")
    w = data[a["weight"]].to_numpy(float)
    y = data[a["outcome"]].to_numpy(float)
    method = a["method"]
    if method == "survey_mean":
        estimate = float(np.sum(w*y)/sum(w))
        score = np.zeros((len(frame), 1))
        score[valid.to_numpy(), 0] = w*(y-estimate)/sum(w)
        covariance, df = survey_meat(score, frame, a)
        se = float(np.sqrt(covariance[0, 0]))
        q = stats.t.ppf((1+a.get("confidence", .95))/2, df)
        lo, hi = estimate-q*se, estimate+q*se
        if set(data[a["outcome"]].unique()) <= {0, 1}:
            lo, hi = max(0., lo), min(1., hi)
        records = [{"term": a["outcome"], "estimate": estimate, "se": se, "lower": lo, "upper": hi}]
    else:
        x = design_matrix(data, a)
        logistic = method == "survey_logistic"
        if logistic:
            binary(data[a["outcome"]], "outcome")
            if data[a["outcome"]].nunique() < 2:
                raise ValueError("Survey logistic regression requires both outcome classes")
        fit = sm.GLM(y, x, family=sm.families.Binomial() if logistic else sm.families.Gaussian(), freq_weights=w).fit()
        if not fit.converged:
            raise ValueError("Survey regression did not converge")
        mu = np.asarray(fit.predict(x))
        bread = x.to_numpy().T @ ((w*mu*(1-mu) if logistic else w)[:, None]*x.to_numpy())
        scores = np.zeros((len(frame), x.shape[1]))
        scores[valid.to_numpy()] = x.to_numpy()*(w*(y-mu))[:, None]
        meat, df = survey_meat(scores, frame, a)
        inv = np.linalg.inv(bread)
        covariance = inv @ meat @ inv.T
        se = np.sqrt(np.maximum(np.diag(covariance), 0))
        q = stats.t.ppf((1+a.get("confidence", .95))/2, df)
        records = [{"term": name, "estimate": fit.params[name], "se": se[i],
                    "lower": fit.params[name]-q*se[i], "upper": fit.params[name]+q*se[i],
                    "p_value": 2*stats.t.sf(abs(fit.params[name]/se[i]), df)} for i, name in enumerate(x.columns)]
    return {"method": method, "flow": {"input_rows": len(frame), "analysis_rows": len(data),
                "excluded_missing_or_domain": len(frame)-len(data)},
            "coefficients": records, "design_df": df,
            "limitations": ["Taylor linearization with replacement; no finite population correction",
                "Weights and cross-cycle adjustments must be supplied correctly; domain keeps full design PSUs"]}


def feature_pipeline(a: dict, model):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    features = a["covariates"]
    categorical = a.get("categorical", [])
    transformers = []
    if numeric_columns := [c for c in features if c not in categorical]:
        transformers.append(("numeric", Pipeline([("impute", SimpleImputer(strategy="median")),
                                                  ("scale", StandardScaler())]), numeric_columns))
    if categorical:
        transformers.append(("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]), categorical))
    return Pipeline([("features", ColumnTransformer(transformers)), ("model", model)])


def predict(frame: pd.DataFrame, a: dict) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.model_selection import GroupShuffleSplit
    roles = [a["outcome"], a["cluster_key"]] + ([a["split_column"]] if a["split"] == "temporal" else [])
    data = frame.dropna(subset=roles).copy()
    if a.get("missing", "complete_case") == "complete_case":
        data = data.dropna(subset=a["covariates"])
    binary(data[a["outcome"]], "outcome")
    numeric(data, [c for c in a["covariates"] if c not in a.get("categorical", [])])
    if any(data[c].nunique() > 50 for c in a.get("categorical", [])):
        raise ValueError("Categorical model features are limited to 50 observed levels")
    fraction, seed = a.get("test_fraction", .25), a.get("seed", 2026)
    if len(data) < 30 or data[a["cluster_key"]].nunique() < 10:
        raise ValueError("Prediction requires at least 30 rows and 10 independent groups")
    if a["split"] == "temporal":
        ordered = data.sort_values(a["split_column"])
        cutoff = ordered[a["split_column"]].iloc[int(len(ordered)*(1-fraction))]
        train = ordered[ordered[a["split_column"]] < cutoff]
        test = ordered[ordered[a["split_column"]] >= cutoff]
        train = train[~train[a["cluster_key"]].isin(test[a["cluster_key"]])]
    else:
        train_idx, test_idx = next(GroupShuffleSplit(n_splits=1, test_size=fraction, random_state=seed).split(
            data, groups=data[a["cluster_key"]]))
        train, test = data.iloc[train_idx], data.iloc[test_idx]
    if min(len(train), len(test)) < 5 or min(train[a["outcome"]].nunique(), test[a["outcome"]].nunique()) < 2:
        raise ValueError("Prediction splits need sufficient observations and both outcome classes")
    if set(train[a["cluster_key"]]) & set(test[a["cluster_key"]]):
        raise ValueError("Group leakage detected")
    model = feature_pipeline(a, LogisticRegression(max_iter=2000, random_state=seed))
    model.fit(train[a["covariates"]], train[a["outcome"]])
    probability = model.predict_proba(test[a["covariates"]])[:, 1]
    y = test[a["outcome"]].to_numpy()
    calibration = []
    bins = pd.qcut(probability, q=min(10, len(test)), duplicates="drop")
    for _, part in pd.DataFrame({"p": probability, "y": y}).groupby(bins, observed=True):
        calibration.append({"n": len(part), "predicted": part.p.mean(), "observed": part.y.mean()})
    return {"method": "predict", "flow": {"input_rows": len(frame), "analysis_rows": len(data),
        "training_rows": len(train), "test_rows": len(test)},
        "metrics": {"auc": roc_auc_score(y, probability), "brier": brier_score_loss(y, probability)},
        "calibration": calibration, "split": a["split"], "group_overlap": 0, "seed": seed,
        "limitations": ["Internal held-out discrimination/calibration; not external validation",
            "Imputation, scaling and encoding are fitted on training rows only"]}


def aipw(frame: pd.DataFrame, a: dict) -> dict:
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.model_selection import StratifiedGroupKFold
    data = frame.dropna(subset=[a["outcome"], a["exposure"], a["cluster_key"]]).copy()
    if a.get("missing", "complete_case") == "complete_case":
        data = data.dropna(subset=a["covariates"])
    numeric(data, [a["outcome"], a["exposure"]] + [c for c in a["covariates"] if c not in a.get("categorical", [])])
    binary(data[a["exposure"]], "exposure")
    if any(data[c].nunique() > 50 for c in a.get("categorical", [])):
        raise ValueError("Categorical model features are limited to 50 observed levels")
    if a["outcome_type"] == "binary":
        binary(data[a["outcome"]], "outcome")
    seed, folds = a.get("seed", 2026), a.get("folds", 3)
    if len(data) < 30 or min(data.groupby(a["exposure"])[a["cluster_key"]].nunique()) < folds:
        raise ValueError("Insufficient independent treatment groups for cross-fitting")
    t, y = data[a["exposure"]].to_numpy(float), data[a["outcome"]].to_numpy(float)
    p, m0, m1 = np.zeros(len(data)), np.zeros(len(data)), np.zeros(len(data))
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    for train, test in splitter.split(data, t, groups=data[a["cluster_key"]]):
        source, target = data.iloc[train], data.iloc[test]
        if source[a["exposure"]].nunique() < 2:
            raise ValueError("A causal training fold lacks treatment overlap")
        propensity = feature_pipeline(a, LogisticRegression(max_iter=2000, random_state=seed))
        propensity.fit(source[a["covariates"]], source[a["exposure"]])
        p[test] = propensity.predict_proba(target[a["covariates"]])[:, 1]
        for treatment, destination in ((0, m0), (1, m1)):
            subset = source[source[a["exposure"]] == treatment]
            if len(subset) < 5:
                raise ValueError("Insufficient outcome-model training observations")
            estimator = LogisticRegression(max_iter=2000, random_state=seed) if a["outcome_type"] == "binary" else Ridge(alpha=1.)
            if a["outcome_type"] == "binary" and subset[a["outcome"]].nunique() < 2:
                raise ValueError("Binary nuisance fold requires both outcome classes")
            outcome_model = feature_pipeline(a, estimator)
            outcome_model.fit(subset[a["covariates"]], subset[a["outcome"]])
            destination[test] = (outcome_model.predict_proba(target[a["covariates"]])[:, 1]
                                 if a["outcome_type"] == "binary" else outcome_model.predict(target[a["covariates"]]))
    clip = a.get("propensity_clip", .01)
    clipped_fraction = np.mean((p < clip) | (p > 1-clip))
    propensity = np.clip(p, clip, 1-clip)
    pseudo = m1-m0 + t*(y-m1)/propensity - (1-t)*(y-m0)/(1-propensity)
    estimate = float(pseudo.mean())
    contributions = pd.Series(pseudo-estimate).groupby(data[a["cluster_key"]].to_numpy()).sum()
    clusters = len(contributions)
    se = np.sqrt(clusters/(clusters-1)*np.sum(contributions**2))/len(data)
    q = stats.t.ppf((1+a.get("confidence", .95))/2, clusters-1)
    return {"method": "aipw", "flow": {"input_rows": len(frame), "analysis_rows": len(data)},
        "coefficients": [{"term": "ATE", "estimate": estimate, "se": se,
                          "lower": estimate-q*se, "upper": estimate+q*se}],
        "overlap": {"propensity_min": p.min(), "propensity_max": p.max(),
                    "clipped_fraction": clipped_fraction, "clip": clip},
        "folds": folds, "independent_clusters": clusters,
        "limitations": ["ATE interpretation is conditional on declared identification assumptions",
            "Cross-fitting and robust variance do not establish absence of unmeasured confounding",
            "Propensity clipping changes finite-sample estimates and requires sensitivity analysis"]}


def analyze(frame: pd.DataFrame, analysis: dict) -> dict:
    method = analysis["method"]
    dispatch = {"describe": describe, "linear": regression, "logistic": regression,
        "kaplan_meier": survival, "cox": survival, "competing_risk": survival,
        "survey_mean": survey, "survey_linear": survey, "survey_logistic": survey,
        "predict": predict, "aipw": aipw}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = dispatch[method](frame, analysis)
    # Warning texts can echo individual values; retain only warning categories.
    result["warnings"] = sorted({type(w.message).__name__ for w in caught})
    return clean(result)
