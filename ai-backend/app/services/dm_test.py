"""Diebold–Mariano-style paired loss tests for embedding retrieval comparison."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from app.config import DATA_EVAL, MODELS, RESULTS_DIR

_CSV_ENCODING = "utf-8-sig"

DM_EXPLANATION = (
    "This is a lag-0 Diebold–Mariano test on paired retrieval losses. "
    "Query order is not temporal, so no forecast horizon or "
    "autocorrelation adjustment is used. Recall@5 is converted into "
    "loss using loss = 1 - Recall@5. A Holm-adjusted p-value below "
    "0.05 indicates that the performance difference is statistically "
    "significant."
)

METHODOLOGICAL_NOTE = (
    "Evaluation queries include augmented paraphrases that share seed families "
    "and may not be fully independent. Augmented query-level DM treats each "
    "query as one observation; seed-family-level DM averages Recall@5 within "
    "each source_seed_id and is the robustness check. Their p-values must not "
    "be combined. Method: lag-0 DM with loss = 1 - Recall@5."
)

PAIRWISE_COLUMNS = [
    "metric",
    "model_a",
    "model_b",
    "paired_n",
    "missing_query_count",
    "mean_loss_a",
    "mean_loss_b",
    "mean_loss_difference_a_minus_b",
    "recall_difference_a_minus_b",
    "dm_statistic",
    "raw_p_value",
    "holm_adjusted_p_value",
    "significant_after_holm",
    "observed_better_model",
    "status",
    "interpretation",
]


def dm_test_retrieval(loss_a, loss_b):
    """
    Diebold-Mariano-style paired loss differential test
    for independent query-level retrieval losses.

    H0:
        Mean(loss_a - loss_b) = 0
    """
    a = np.asarray(loss_a, dtype=float)
    b = np.asarray(loss_b, dtype=float)

    if a.shape != b.shape:
        raise ValueError(
            "Model A and Model B must contain the same number "
            "of paired query-level losses."
        )

    valid = np.isfinite(a) & np.isfinite(b)
    a = a[valid]
    b = b[valid]

    if len(a) < 2:
        raise ValueError(
            "At least two valid paired observations are required."
        )

    differences = a - b
    n = len(differences)

    mean_loss_a = float(np.mean(a))
    mean_loss_b = float(np.mean(b))
    mean_difference = float(np.mean(differences))
    standard_deviation = float(np.std(differences, ddof=1))

    if np.isclose(standard_deviation, 0):
        if np.isclose(mean_difference, 0):
            return {
                "paired_n": n,
                "mean_loss_a": mean_loss_a,
                "mean_loss_b": mean_loss_b,
                "mean_loss_difference_a_minus_b": mean_difference,
                "dm_statistic": 0.0,
                "raw_p_value": 1.0,
                "status": "identical_query_level_loss_differences",
            }

        return {
            "paired_n": n,
            "mean_loss_a": mean_loss_a,
            "mean_loss_b": mean_loss_b,
            "mean_loss_difference_a_minus_b": mean_difference,
            "dm_statistic": np.inf if mean_difference > 0 else -np.inf,
            "raw_p_value": 0.0,
            "status": "constant_nonzero_loss_difference",
        }

    standard_error = standard_deviation / np.sqrt(n)
    dm_statistic = mean_difference / standard_error

    raw_p_value = 2 * stats.t.sf(
        np.abs(dm_statistic),
        df=n - 1,
    )

    return {
        "paired_n": n,
        "mean_loss_a": mean_loss_a,
        "mean_loss_b": mean_loss_b,
        "mean_loss_difference_a_minus_b": mean_difference,
        "dm_statistic": float(dm_statistic),
        "raw_p_value": float(raw_p_value),
        "status": "completed",
    }


def build_interpretation(
    row: dict[str, Any] | pd.Series,
    observation_level: str = "query-level",
) -> str:
    if row.get("status") != "completed":
        return f"Test not completed: {row.get('status')}."

    model_a = row["model_a"]
    model_b = row["model_b"]
    better = row["observed_better_model"]
    adjusted_p = row["holm_adjusted_p_value"]
    recall_difference = row["recall_difference_a_minus_b"]

    if pd.isna(adjusted_p):
        return "Adjusted p-value is unavailable."

    if adjusted_p < 0.05:
        return (
            f"{better} achieved lower {observation_level} Recall@5 loss. "
            f"The difference between {model_a} and {model_b} "
            f"was statistically significant after Holm correction "
            f"(adjusted p={adjusted_p:.6f}, "
            f"Recall@5 difference A-B={recall_difference:.4f})."
        )

    return (
        f"{better} had the better observed {observation_level} Recall@5 "
        f"result, but no statistically significant difference "
        f"was detected between {model_a} and {model_b} after "
        f"Holm correction "
        f"(adjusted p={adjusted_p:.6f}, "
        f"Recall@5 difference A-B={recall_difference:.4f})."
    )


def apply_holm_correction(
    dm_results: pd.DataFrame,
    observation_level: str = "query-level",
) -> pd.DataFrame:
    out = dm_results.copy()
    if "holm_adjusted_p_value" not in out.columns:
        out["holm_adjusted_p_value"] = np.nan
    if "significant_after_holm" not in out.columns:
        out["significant_after_holm"] = False

    valid_mask = out["status"].eq("completed") & out["raw_p_value"].notna()
    if not valid_mask.any():
        out["interpretation"] = out.apply(
            lambda row: build_interpretation(row, observation_level),
            axis=1,
        )
        return out

    valid_p_values = out.loc[valid_mask, "raw_p_value"]
    reject, adjusted_p_values, _, _ = multipletests(
        valid_p_values,
        alpha=0.05,
        method="holm",
    )
    out.loc[valid_mask, "holm_adjusted_p_value"] = adjusted_p_values
    out.loc[valid_mask, "significant_after_holm"] = reject
    out["interpretation"] = out.apply(
        lambda row: build_interpretation(row, observation_level),
        axis=1,
    )
    return out


def run_pairwise_dm(
    wide_loss: pd.DataFrame,
    observation_level: str = "query-level",
) -> pd.DataFrame:
    """Run all pairwise lag-0 DM tests on an id × model loss matrix."""
    models = list(wide_loss.columns)
    pairwise_results: list[dict[str, Any]] = []

    for model_a, model_b in combinations(models, 2):
        pair = wide_loss[[model_a, model_b]].copy()
        missing_count = int(pair.isna().any(axis=1).sum())
        paired = pair.dropna()

        if len(paired) < 2:
            pairwise_results.append(
                {
                    "metric": "recall_at_5",
                    "model_a": model_a,
                    "model_b": model_b,
                    "paired_n": len(paired),
                    "missing_query_count": missing_count,
                    "mean_loss_a": np.nan,
                    "mean_loss_b": np.nan,
                    "mean_loss_difference_a_minus_b": np.nan,
                    "recall_difference_a_minus_b": np.nan,
                    "dm_statistic": np.nan,
                    "raw_p_value": np.nan,
                    "observed_better_model": "",
                    "status": "insufficient_paired_queries",
                }
            )
            continue

        test_result = dm_test_retrieval(
            paired[model_a].to_numpy(),
            paired[model_b].to_numpy(),
        )
        mean_difference = test_result["mean_loss_difference_a_minus_b"]
        if mean_difference < 0:
            observed_better_model = model_a
        elif mean_difference > 0:
            observed_better_model = model_b
        else:
            observed_better_model = "tie"

        pairwise_results.append(
            {
                "metric": "recall_at_5",
                "model_a": model_a,
                "model_b": model_b,
                "paired_n": test_result["paired_n"],
                "missing_query_count": missing_count,
                "mean_loss_a": test_result["mean_loss_a"],
                "mean_loss_b": test_result["mean_loss_b"],
                "mean_loss_difference_a_minus_b": mean_difference,
                "recall_difference_a_minus_b": -mean_difference,
                "dm_statistic": test_result["dm_statistic"],
                "raw_p_value": test_result["raw_p_value"],
                "observed_better_model": observed_better_model,
                "status": test_result["status"],
            }
        )

    dm_results = pd.DataFrame(pairwise_results)
    dm_results = apply_holm_correction(dm_results, observation_level)
    for col in PAIRWISE_COLUMNS:
        if col not in dm_results.columns:
            dm_results[col] = np.nan
    return dm_results[PAIRWISE_COLUMNS]


def _has_relevant_chunks(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and np.isnan(value):
        return False
    if isinstance(value, list):
        return any(str(x).strip() for x in value)
    text = str(value).strip()
    if not text or text in ("[]", "nan", "None"):
        return False
    if text.startswith("["):
        try:
            parsed = json.loads(text.replace("'", '"'))
            if isinstance(parsed, list):
                return any(str(x).strip() for x in parsed)
        except json.JSONDecodeError:
            pass
    return bool([part for part in text.split("|") if part.strip()])


def load_eval_metadata() -> pd.DataFrame:
    """Load seed_id and empty-GT flags from queries.jsonl (fallback: queries.csv)."""
    jsonl_path = DATA_EVAL / "queries.jsonl"
    csv_path = DATA_EVAL / "queries.csv"

    if jsonl_path.exists():
        rows: list[dict[str, Any]] = []
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                query_id = str(obj.get("query_id") or obj.get("eval_id") or "").strip()
                if not query_id:
                    continue
                rows.append(
                    {
                        "query_id": query_id,
                        "source_seed_id": str(obj.get("seed_id") or "").strip(),
                        "has_ground_truth": _has_relevant_chunks(
                            obj.get("relevant_chunk_ids")
                        ),
                    }
                )
        meta = pd.DataFrame(rows)
    elif csv_path.exists():
        raw = pd.read_csv(csv_path, encoding=_CSV_ENCODING)
        id_col = "eval_id" if "eval_id" in raw.columns else "query_id"
        meta = pd.DataFrame(
            {
                "query_id": raw[id_col].astype(str),
                "source_seed_id": raw.get("seed_id", pd.Series([""] * len(raw)))
                .fillna("")
                .astype(str),
                "has_ground_truth": True,
            }
        )
    else:
        raise FileNotFoundError(
            f"Missing eval metadata: expected {jsonl_path} or {csv_path}"
        )

    meta = meta.drop_duplicates(subset=["query_id"], keep="first")
    return meta


def load_embedding_query_results(
    model_keys: list[str] | None = None,
) -> pd.DataFrame:
    """Build long query-level table from per_query_metrics.csv for each model."""
    keys = model_keys or list(MODELS.keys())
    frames: list[pd.DataFrame] = []

    for key in keys:
        if key not in MODELS:
            raise ValueError(f"Unknown model key: {key}")
        display_name = MODELS[key]["display_name"]
        path = RESULTS_DIR / display_name / "per_query_metrics.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing per-query metrics: {path}")

        df = pd.read_csv(path, encoding=_CSV_ENCODING)
        if "query_id" not in df.columns:
            raise ValueError(f"{path} is missing query_id")
        if "recall@5" not in df.columns:
            raise ValueError(f"{path} is missing recall@5")

        part = pd.DataFrame(
            {
                "query_id": df["query_id"].astype(str),
                "model": display_name,
                "recall_at_5": pd.to_numeric(df["recall@5"], errors="coerce"),
            }
        )
        frames.append(part)

    results_df = pd.concat(frames, ignore_index=True)

    duplicates = results_df.duplicated(subset=["query_id", "model"], keep=False)
    if duplicates.any():
        duplicate_rows = results_df.loc[duplicates, ["query_id", "model"]]
        raise ValueError(
            "Duplicate query_id + model rows detected:\n"
            + duplicate_rows.to_string(index=False)
        )

    meta = load_eval_metadata()
    results_df = results_df.merge(meta, on="query_id", how="left")
    results_df["has_ground_truth"] = results_df["has_ground_truth"].fillna(False)
    results_df["source_seed_id"] = results_df["source_seed_id"].fillna("")
    results_df["loss_recall_at_5"] = 1.0 - results_df["recall_at_5"]
    return results_df


def filter_valid_for_dm(results_df: pd.DataFrame) -> pd.DataFrame:
    """Exclude empty-GT and non-finite recall rows equally across models."""
    return results_df[
        results_df["has_ground_truth"]
        & results_df["recall_at_5"].notna()
        & np.isfinite(results_df["recall_at_5"])
    ].copy()


def wide_loss_from_long(
    results_df: pd.DataFrame,
    id_col: str = "query_id",
) -> pd.DataFrame:
    required_columns = {id_col, "model", "loss_recall_at_5"}
    missing_columns = required_columns - set(results_df.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {sorted(missing_columns)}")

    return results_df.pivot(
        index=id_col,
        columns="model",
        values="loss_recall_at_5",
    )


def build_seed_level_results(results_df: pd.DataFrame) -> pd.DataFrame:
    if "source_seed_id" not in results_df.columns:
        raise ValueError("source_seed_id is required for seed-level DM analysis")

    seeded = results_df[
        results_df["source_seed_id"].astype(str).str.strip().ne("")
    ].copy()
    seed_level = (
        seeded.groupby(["source_seed_id", "model"], as_index=False)["recall_at_5"]
        .mean()
    )
    seed_level["loss_recall_at_5"] = 1.0 - seed_level["recall_at_5"]
    return seed_level


def build_summary(
    results_df: pd.DataFrame,
    query_level_dm: pd.DataFrame,
    seed_level_dm: pd.DataFrame | None,
    *,
    excluded_empty_gt: int,
    has_seed_analysis: bool,
    seeds_before: int = 0,
    seeds_after: int = 0,
    excluded_seed_ids: list[str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for model, group in results_df.groupby("model"):
        rows.append(
            {
                "section": "per_model",
                "model": model,
                "n_queries": int(len(group)),
                "mean_recall_at_5": float(group["recall_at_5"].mean()),
                "mean_loss_recall_at_5": float(group["loss_recall_at_5"].mean()),
                "pairwise_tests": "",
                "significant_pairs_after_holm": "",
                "note": "",
            }
        )

    sig_q = int(query_level_dm.get("significant_after_holm", pd.Series(dtype=bool)).fillna(False).sum())
    rows.append(
        {
            "section": "query_level_dm",
            "model": "",
            "n_queries": int(query_level_dm["paired_n"].iloc[0]) if len(query_level_dm) else 0,
            "mean_recall_at_5": np.nan,
            "mean_loss_recall_at_5": np.nan,
            "pairwise_tests": int(len(query_level_dm)),
            "significant_pairs_after_holm": sig_q,
            "note": METHODOLOGICAL_NOTE,
        }
    )

    if has_seed_analysis and seed_level_dm is not None and len(seed_level_dm):
        sig_s = int(
            seed_level_dm.get("significant_after_holm", pd.Series(dtype=bool))
            .fillna(False)
            .sum()
        )
        rows.append(
            {
                "section": "seed_level_dm",
                "model": "",
                "n_queries": int(seed_level_dm["paired_n"].iloc[0]),
                "mean_recall_at_5": np.nan,
                "mean_loss_recall_at_5": np.nan,
                "pairwise_tests": int(len(seed_level_dm)),
                "significant_pairs_after_holm": sig_s,
                "note": "Seed-family-level robustness analysis; p-values not combined with query-level.",
            }
        )
    else:
        rows.append(
            {
                "section": "seed_level_dm",
                "model": "",
                "n_queries": 0,
                "mean_recall_at_5": np.nan,
                "mean_loss_recall_at_5": np.nan,
                "pairwise_tests": 0,
                "significant_pairs_after_holm": 0,
                "note": (
                    "source_seed_id unavailable; only query-level DM was run. "
                    + METHODOLOGICAL_NOTE
                ),
            }
        )

    excluded_ids = excluded_seed_ids or []
    excluded_ids_text = ", ".join(excluded_ids) if excluded_ids else "none"
    rows.append(
        {
            "section": "filters",
            "model": "",
            "n_queries": excluded_empty_gt,
            "mean_recall_at_5": np.nan,
            "mean_loss_recall_at_5": np.nan,
            "pairwise_tests": "",
            "significant_pairs_after_holm": "",
            "note": (
                f"Excluded {excluded_empty_gt} empty-ground-truth queries equally "
                f"from all models. Unique source_seed_id before filter: {seeds_before}; "
                f"after filter: {seeds_after}; excluded seed families: {excluded_ids_text} "
                f"({seeds_before} - {len(excluded_ids)} = {seeds_after}). "
                f"Method: lag-0 DM, loss = 1 - Recall@5 (query order is not temporal)."
            ),
        }
    )
    return pd.DataFrame(rows)


def run_full_dm_analysis(
    model_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Load metrics, run query- and seed-level DM, write result CSVs."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_embedding_query_results(model_keys)
    before_n = int(raw["query_id"].nunique())
    empty_ids = set(raw.loc[~raw["has_ground_truth"], "query_id"].astype(str))
    excluded_empty_gt = len(empty_ids)

    seeds_before_set = set(
        raw.loc[raw["source_seed_id"].astype(str).str.strip().ne(""), "source_seed_id"]
        .astype(str)
        .unique()
    )
    filtered = filter_valid_for_dm(raw)
    after_n = int(filtered["query_id"].nunique())
    seeds_after_set = set(
        filtered.loc[
            filtered["source_seed_id"].astype(str).str.strip().ne(""),
            "source_seed_id",
        ]
        .astype(str)
        .unique()
    )
    excluded_seed_ids = sorted(seeds_before_set - seeds_after_set)

    # Persist long table used for DM (filtered to analyzable queries).
    export_cols = [
        "query_id",
        "model",
        "recall_at_5",
        "loss_recall_at_5",
        "source_seed_id",
        "has_ground_truth",
    ]
    embedding_path = RESULTS_DIR / "embedding_query_results.csv"
    filtered[export_cols].to_csv(embedding_path, index=False, encoding="utf-8")

    wide_query = wide_loss_from_long(filtered, id_col="query_id")
    query_level_dm = run_pairwise_dm(
        wide_query,
        observation_level="query-level",
    )
    query_path = RESULTS_DIR / "dm_test_recall_at_5_pairwise.csv"
    query_level_dm.to_csv(query_path, index=False, encoding="utf-8")

    has_seed = (
        filtered["source_seed_id"].astype(str).str.strip().ne("").any()
    )
    seed_level_dm: pd.DataFrame | None = None
    seed_path = RESULTS_DIR / "dm_test_seed_level_recall_at_5_pairwise.csv"
    if has_seed:
        seed_long = build_seed_level_results(filtered)
        wide_seed = seed_long.pivot(
            index="source_seed_id",
            columns="model",
            values="loss_recall_at_5",
        )
        seed_level_dm = run_pairwise_dm(
            wide_seed,
            observation_level="seed-family-level",
        )
        seed_level_dm.to_csv(seed_path, index=False, encoding="utf-8")
    elif seed_path.exists():
        seed_path.unlink()

    summary = build_summary(
        filtered,
        query_level_dm,
        seed_level_dm,
        excluded_empty_gt=excluded_empty_gt,
        has_seed_analysis=has_seed,
        seeds_before=len(seeds_before_set),
        seeds_after=len(seeds_after_set),
        excluded_seed_ids=excluded_seed_ids,
    )
    summary_path = RESULTS_DIR / "dm_test_recall_at_5_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8")

    return {
        "embedding_query_results_path": str(embedding_path),
        "query_level_path": str(query_path),
        "seed_level_path": str(seed_path) if has_seed else None,
        "summary_path": str(summary_path),
        "queries_before_filter": before_n,
        "queries_after_filter": after_n,
        "excluded_empty_gt": excluded_empty_gt,
        "has_seed_analysis": has_seed,
        "query_level": query_level_dm,
        "seed_level": seed_level_dm,
        "summary": summary,
    }


def _read_csv_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    df = pd.read_csv(path, encoding=_CSV_ENCODING)
    # Normalize booleans / NaN for JSON
    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    for row in records:
        for key, value in list(row.items()):
            if isinstance(value, (np.floating, float)) and (
                value is not None and not np.isfinite(value)
            ):
                row[key] = None
            elif isinstance(value, (np.bool_, bool)):
                row[key] = bool(value)
            elif isinstance(value, np.integer):
                row[key] = int(value)
            elif isinstance(value, np.floating):
                row[key] = float(value)
    return records


def load_dm_test_payload() -> dict[str, Any]:
    """Load persisted DM outputs for the API/UI."""
    query_path = RESULTS_DIR / "dm_test_recall_at_5_pairwise.csv"
    seed_path = RESULTS_DIR / "dm_test_seed_level_recall_at_5_pairwise.csv"
    summary_path = RESULTS_DIR / "dm_test_recall_at_5_summary.csv"

    query_level = _read_csv_records(query_path)
    seed_level = _read_csv_records(seed_path)
    summary_rows = _read_csv_records(summary_path)

    summary: dict[str, Any] = {
        "available": bool(query_level),
        "has_seed_analysis": bool(seed_level),
        "methodological_note": METHODOLOGICAL_NOTE,
        "rows": summary_rows,
        "hint": (
            None
            if query_level
            else "DM results not found. Run: python backend/scripts/run_diebold_mariano.py"
        ),
    }

    # Flatten a few convenience fields from summary CSV
    for row in summary_rows:
        if row.get("section") == "query_level_dm":
            summary["query_level_paired_n"] = row.get("n_queries")
            summary["query_level_significant_pairs"] = row.get(
                "significant_pairs_after_holm"
            )
        if row.get("section") == "seed_level_dm":
            summary["seed_level_paired_n"] = row.get("n_queries")
            summary["seed_level_significant_pairs"] = row.get(
                "significant_pairs_after_holm"
            )
        if row.get("section") == "filters":
            summary["excluded_empty_gt"] = row.get("n_queries")

    return {
        "query_level": query_level,
        "seed_level": seed_level,
        "summary": summary,
        "explanation": DM_EXPLANATION,
    }
