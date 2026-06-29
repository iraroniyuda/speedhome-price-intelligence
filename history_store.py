import json
from pathlib import Path
from typing import Dict, List

import pandas as pd


HISTORY_FILE = Path("data") / "scrape_history.json"


def _ensure_data_dir():
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_scrape_history() -> Dict:
    if not HISTORY_FILE.exists():
        return {}

    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_scrape_history(history: Dict) -> None:
    _ensure_data_dir()
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_dataset_to_history(
    dataset_key: str,
    target_area: str,
    source_url: str,
    raw_count: int,
    filtered_count: int,
    fetch_method: str,
    cache_used: bool,
    df: pd.DataFrame,
    metadata: Dict,
) -> None:
    history = load_scrape_history()

    safe_df = df.copy()

    # Convert NaN to None for JSON safety.
    records = safe_df.where(pd.notna(safe_df), None).to_dict(orient="records")

    history[dataset_key] = {
        "dataset_key": dataset_key,
        "target_area": target_area,
        "source_url": source_url,
        "raw_count": raw_count,
        "filtered_count": filtered_count,
        "fetch_method": fetch_method,
        "cache_used": cache_used,
        "metadata": metadata,
        "records": records,
    }

    save_scrape_history(history)


def delete_dataset_from_history(dataset_key: str) -> None:
    history = load_scrape_history()

    if dataset_key in history:
        del history[dataset_key]

    save_scrape_history(history)


def clear_scrape_history() -> None:
    save_scrape_history({})


def history_to_options() -> List[str]:
    history = load_scrape_history()

    options = []

    for key, item in history.items():
        label = (
            f"{item.get('target_area', key)} "
            f"({item.get('filtered_count', 0)} listings, "
            f"{item.get('fetch_method', '-')})"
        )
        options.append(label)

    return options


def get_dataset_key_from_label(label: str) -> str:
    history = load_scrape_history()

    for key, item in history.items():
        expected_label = (
            f"{item.get('target_area', key)} "
            f"({item.get('filtered_count', 0)} listings, "
            f"{item.get('fetch_method', '-')})"
        )

        if label == expected_label:
            return key

    return label


def load_selected_datasets(dataset_keys: List[str]) -> Dict[str, pd.DataFrame]:
    history = load_scrape_history()
    datasets = {}

    for key in dataset_keys:
        item = history.get(key)

        if not item:
            continue

        records = item.get("records", [])
        df = pd.DataFrame(records)

        if df.empty:
            continue

        df["comparison_area"] = item.get("target_area", key)
        df["comparison_source_url"] = item.get("source_url", "-")
        df["comparison_fetch_method"] = item.get("fetch_method", "-")
        df["comparison_cache_used"] = item.get("cache_used", False)

        datasets[key] = df

    return datasets
