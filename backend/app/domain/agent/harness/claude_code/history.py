"""Read retained completed intervals independently of the output landing cursor."""

from app.domain.agent.harness.claude_code.journal import Journal


def landed_results(path):
    journal = Journal(path)
    try:
        landed = int(journal.recall("landed") or 0)
        after = 0
        results = []
        while page := journal.read(after):
            for entry in page:
                if entry["sequence"] > landed:
                    return results
                record = entry["record"]
                if (
                    record.get("type") == "result"
                    and not record.get("is_error")
                    and not (record.get("cheese") or {}).get("interrupted")
                ):
                    results.append(record)
            after = page[-1]["sequence"]
        return results
    finally:
        journal.close()
