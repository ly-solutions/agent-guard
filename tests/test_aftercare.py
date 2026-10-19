from conftest import bash, denied, hook


def aftercare(command: str, response: object) -> dict | None:
    return hook("post-bash", {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": response})


def test_advice_is_a_command_the_guard_accepts() -> None:
    advice = aftercare("kubectl -n shop-prod exec -i db-0 -- psql -f - < q.sql", {"stdout": "error: unexpected EOF"})
    text = advice["hookSpecificOutput"]["additionalContext"]
    command = text.split("`")[1].replace("<ns>", "shop-prod").replace("<pod>", "db-0").replace("<task>", "PROJ-1")
    assert command.startswith("kubectl -n shop-prod exec db-0") and not denied(bash(command)), command


def test_broken_prod_psql_exec_tells_to_terminate_the_orphan() -> None:
    prod = "kubectl -n shop-prod exec -i db-0 -c postgres -- psql -f - < q.sql > out.csv"
    for broken in [
        {"stdout": "Command did not complete within its 120s timeout and was moved to the background (ID: x)"},
        {"stderr": "error: Internal error occurred: error executing command in container: read tcp 10.0.0.1:1->10.96.0.1:443: connection reset by peer"},
        {"stdout": "error: unexpected EOF"},
    ]:
        assert "pg_terminate_backend" in aftercare(prod, broken)["hookSpecificOutput"]["additionalContext"]
    assert aftercare(prod, {"stdout": "62851\n"}) is None
    assert aftercare("kubectl -n shop-test exec -i db-0 -- psql -f - < q.sql", {"stdout": "moved to the background"}) is None
    assert aftercare("pnpm vitest run | tail", {"stdout": "Command timed out"}) is None
