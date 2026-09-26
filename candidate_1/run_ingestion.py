import argparse

from dotenv import load_dotenv      #type: ignore

load_dotenv()

from src.pipeline import ingest_organization


def main():
    parser = argparse.ArgumentParser(
        description="Run GitHub ingestion for one or more organizations"
    )
    parser.add_argument(
        "--org",
        action="append",
        required=True,
        help="Organization to ingest. Repeat this flag to load multiple orgs, e.g. --org stripe --org shopify",
    )
    args = parser.parse_args()

    for organization in args.org:
        print(f"starting ingestion for {organization}")
        result = ingest_organization(organization)
        print(
            f"{organization}: fetched={result['records_fetched']} "
            f"valid={result['records_valid']} "
            f"quarantined={result['records_quarantined']} "
            f"status={result['status']}"
        )


if __name__ == "__main__":
    main()
