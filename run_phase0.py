"""Phase 0 spine check: fixture CSV -> DuckDB (L3) -> PM4Py variant analysis (L4)."""

from pathlib import Path

from engine.db import EventLogStore
from mining.variants import variant_analysis

FIXTURE = Path(__file__).parent / "fixtures" / "p2p_event_log.csv"


def main() -> None:
    with EventLogStore() as store:
        n_events = store.load_event_log_csv(FIXTURE)
        df = store.fetch_event_log()

    n_cases = df["case_id"].nunique()
    print(f"Loaded {n_events} events / {n_cases} cases from {FIXTURE.name} into DuckDB\n")

    variants = variant_analysis(df)
    print(f"{'#':<3} {'cases':>5} {'share':>7}  variant")
    for i, v in enumerate(variants, 1):
        print(f"{i:<3} {v['case_count']:>5} {v['share']:>6.0%}  {v['variant']}")


if __name__ == "__main__":
    main()
