import shutil
import subprocess
from pathlib import Path

from conftest import WT, bash, denied, hook, reason, rewritten

PKILL_SHIM = 'pkill() { command pkill -A "$@"; }; export -f pkill'
SPARES_ANCESTORS = shutil.which("pkill") is not None and "--ignore-ancestors" in subprocess.run(["pkill", "--help"], capture_output=True, text=True).stdout
FAKE_GITLAB = "glpat-" + "Ab3dE5gH7jK9mN1pQ3sT"
COMMIT = "git " + "commit"
SAFE_PSQL = (
    "env PGAPPNAME=agent-PROJ-1 PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s "
    "-c idle_in_transaction_session_timeout=60s -c default_transaction_read_only=on'"
)


def test_pkill_f_runs_without_killing_its_own_shell() -> None:
    assert bash("kill 1234") is None
    if not SPARES_ANCESTORS:
        assert denied(bash("pkill -f 'next dev'"))
        return
    for command in (
        "pkill -f 'next dev'",
        "pkill -u dev -f 'python3 (sync|copy).py'",
        "pkill --signal TERM --full sync-loop",
        "cd app && pkill -f vite; ls",
    ):
        assert rewritten(bash(command)) == f"{PKILL_SHIM}\n{command}", command
    # The shim is a shell function: behind a wrapper that execs the binary it would not apply.
    for command in ("timeout 5 pkill -f vite", "sudo pkill -f vite", "sh -c 'pkill -f vite'", "command pkill -f vite"):
        assert denied(bash(command)), command
    # Text in a heredoc is not a command.
    assert bash("cat > notes.md <<'EOF'\ndo not pkill -f vite\nEOF") is None


def test_pkill_shim_spares_the_calling_shell() -> None:
    if not SPARES_ANCESTORS:
        return
    marker = "sleep 3" + "17"
    script = f"{PKILL_SHIM}\n{marker} & pkill -f '{marker}'; wait; echo alive"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0 and "alive" in proc.stdout


def test_force_push_to_a_working_branch_becomes_a_lease() -> None:
    lease = "git push --force-with-lease origin agent/task-1"
    assert rewritten(bash("git push --force origin agent/task-1")) == lease
    assert rewritten(bash("git push -f origin agent/task-1")) == lease
    assert denied(bash("git push --force origin main"))


def test_piped_gate_runs_under_pipefail_and_prints_its_code() -> None:
    command = rewritten(bash("cd app && pnpm vitest run a.test.ts | tail -20"))
    assert command.startswith("set -o pipefail\n")
    assert command.endswith("exit $__gate_exit")
    proc = subprocess.run(["bash", "-c", command.replace("cd app && pnpm vitest run a.test.ts", "false")], capture_output=True, text=True)
    assert proc.returncode == 1 and "[exit code: 1]" in proc.stdout


def test_leaves_plain_commands_alone() -> None:
    assert bash("pnpm vitest run a.test.ts") is None
    assert bash("set -o pipefail; pnpm vitest run | tail -3") is None
    assert bash("grep -rn foo src | head") is None


def test_clears_the_proxy_for_configured_tools_once(site) -> None:
    assert rewritten(bash("kubectl -n x get pods")).startswith("unset HTTP_PROXY HTTPS_PROXY")
    assert bash("unset HTTP_PROXY HTTPS_PROXY; kubectl get ns") is None
    site({"proxy": {"clear_for": []}})
    assert bash("kubectl -n x get pods") is None


DANGEROUS = [
    ("force push short", "git push -fu origin agent/task-1"),
    ("force push into main", "git push --force origin main"),
    ("force push inside bash -c into main", "bash -c 'git push --force origin main'"),
    ("pkill -f behind timeout", "timeout 5 pkill -f 'next dev'"),
    ("force push refspec", "git push origin +agent/task-1"),
    ("lease into main", "git push --force-with-lease origin main"),
    ("kubectl apply prod", "kubectl -n shop-prod apply -f deploy.yaml"),
    ("kubectl delete default", "kubectl delete pod web-1"),
    ("kubectl set image", "kubectl set image deploy/web web=img:2 -n platform"),
    ("prod migration", "kubectl -n shop-prod exec db-0 -- psql -c 'alter table orders add column x int'"),
    ("custom rule", "tsh login --proxy=teleport.example.com"),
    ("rm home", "rm -rf ~"),
    ("rm a shared checkout", "rm -rf /srv/work/web"),
    ("rm root glob", "rm -rf /*"),
    ("rm escaping cd", "cd /tmp && rm -rf /etc/nginx"),
    ("delete agent branch", "git branch -D agent/task-1"),
    ("delete agent branch remote", "git push origin --delete agent/task-1"),
    ("literal token", f"curl -H 'PRIVATE-TOKEN: {FAKE_GITLAB}' https://gitlab.com/api/v4/user"),
    (
        "prod copy to stdout",
        "kubectl -n shop-prod exec -i db-0 -c postgres -- sh -lc 'psql -U u -d d -c \"\\\\copy (select * from orders) to stdout with csv\"' > out.csv",
    ),
    ("prod copy via heredoc", "cat > q.sql <<'SQL'\nCOPY (SELECT id FROM orders) TO STDOUT;\nSQL\nkubectl -n shop-prod exec -i db-0 -- psql -f - < q.sql"),
    ("kill kubectl exec", "pgrep -f 'exec -i db-0' | xargs -r kill"),
    ("kill kubectl by pid subst", "kill $(pgrep -f 'kubectl -n shop-prod exec')"),
    # Wrappers, shell keywords, substitutions and tsh must not hide the command.
    ("behind timeout", "timeout 30 kubectl -n shop-prod delete pod web-1"),
    ("behind timeout with flags", "timeout -k 5 -s KILL 30s kubectl -n shop-prod delete pod web-1"),
    ("inside a for loop", "for p in a b; do kubectl -n shop-prod delete pod $p; done"),
    ("inside until", "until kubectl -n edge delete pod router-1; do sleep 2; done"),
    ("behind xargs", "kubectl -n shop-prod get pods -o name | xargs -n 1 kubectl -n shop-prod delete"),
    ("behind sudo and nice", "sudo -u dev nice -n 5 kubectl -n shop-prod scale deploy/web --replicas=0"),
    ("inside a substitution", "P=$(kubectl -n shop-prod delete pod web-1 -o name)"),
    ("inside backticks", "echo `kubectl -n shop-prod delete pod web-1`"),
    ("inside eval", "eval kubectl -n shop-prod delete pod web-1"),
    ("through tsh", "tsh kubectl -n shop-prod delete pod web-1"),
    ("helm upgrade prod", "helm -n ingress upgrade edge-agent charts/edge-agent --reuse-values"),
    ("helm uninstall default", "helm uninstall network-operator"),
    # The command shape that once left a psql orphan holding a lock for days.
    (
        "psql without limits",
        "kubectl -n shop-prod exec -i orders-postgres-0 -- sh -lc 'PGPASSWORD=$POSTGRES_PASSWORD psql -U $POSTGRES_USER -d $POSTGRES_DB -P pager=off -f -' < /tmp/q.sql",
    ),
    ("psql read-only but unbounded", "kubectl -n shop-prod exec db-0 -- psql -c 'SET default_transaction_read_only=on' -f -"),
    (
        "psql with zero timeout",
        "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent-PROJ-1 PGOPTIONS='-c statement_timeout=0 -c lock_timeout=3s -c default_transaction_read_only=on' psql -c 'select 1'",
    ),
    (
        "psql with an hour of timeout",
        "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent-PROJ-1 PGOPTIONS='-c statement_timeout=1h -c lock_timeout=3s -c default_transaction_read_only=on' psql -c 'select 1'",
    ),
    (
        "psql without a name",
        "kubectl -n shop-prod exec db-0 -- env PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s -c default_transaction_read_only=on' psql -c 'select 1'",
    ),
    (
        "psql write without a task",
        "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s -c default_transaction_read_only=off' psql -c 'update orders set x=1'",
    ),
    ("psql in default namespace", "kubectl exec db-0 -- psql -c 'select 1'"),
    ("psql behind timeout", "timeout 40 kubectl -n platform exec db-0 -- psql -c 'select count(*) from runs'"),
    ("pg_dump", "kubectl -n shop-prod exec db-0 -- pg_dump -U app app > /tmp/dump.sql"),
    ("clickhouse without limits", "kubectl -n shop-prod exec analytics-clickhouse-0 -- clickhouse-client -q 'select count() from events'"),
    (
        "clickhouse write without a task",
        "kubectl -n shop-prod exec analytics-clickhouse-0 -- clickhouse-client --readonly=0 --max_execution_time=60 -q 'truncate table bi.x'",
    ),
    ("port-forward to a database", "kubectl -n shop-prod port-forward svc/orders-postgres 5432:5432"),
    ("port-forward to a database pod", "kubectl -n platform port-forward pod/ingest-postgres-0 15432:5432"),
    ("redis flushall", "kubectl -n shop-prod exec cache-redis-0 -- redis-cli FLUSHALL"),
    ("redis keys", "kubectl -n shop-prod exec cache-redis-0 -- redis-cli keys '*'"),
    ("kafka topic delete", "kubectl -n kafka exec kafka-brokers-0 -- bin/kafka-topics.sh --bootstrap-server localhost:9092 --delete --topic orders.raw"),
    (
        "kafka offsets reset",
        "kubectl -n kafka exec kafka-brokers-0 -- bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group g --reset-offsets --to-earliest --execute --topic t",
    ),
    # Inside the envelope too: a bounded stream to stdout still loses its reader.
    ("copy to stdout inside the envelope", f"kubectl -n shop-prod exec -i db-0 -- {SAFE_PSQL} psql -c 'copy (select 1) to stdout' > out.csv"),
]

NORMAL = [
    "git status -sb",
    "git push -u origin agent/task-1",
    "git push --force-with-lease origin agent/task-1",
    "git branch -d agent/task-1",
    "git branch -D feature/old",
    "git log --oneline -5",
    "git diff --stat",
    "git fetch -q origin main",
    "git rebase origin/main",
    "kubectl -n platform get pods",
    "kubectl -n shop-prod logs deploy/web --tail=50",
    "kubectl -n shop-test apply -f x.yaml",
    "kubectl -n shop-prod rollout status deploy/web",
    "kubectl -n shop-prod describe pod web-1",
    "kubectl -n shop-test delete pod probe",
    f"kubectl -n platform exec db-0 -- {SAFE_PSQL} psql -c 'select count(*) from runs'",
    "kubectl get ns",
    "tsh status",
    "tbot start -c x.yaml",
    # The safe envelope, and everything the database rules must leave alone.
    f'kubectl -n shop-prod exec -i orders-postgres-0 -c postgres -- {SAFE_PSQL} sh -c \'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -f -\' < /tmp/q.sql',
    "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent-TASK-12 PGOPTIONS='-c statement_timeout=30s -c lock_timeout=3s -c default_transaction_read_only=on' psql -Atc 'select pg_terminate_backend(42)'",
    "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent-TASK-7 PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s -c default_transaction_read_only=off' psql -c 'update account set fee=5 where id=1'",
    "kubectl -n shop-prod exec db-0 -- psql -c 'set statement_timeout to 60000; set lock_timeout = 3000; set default_transaction_read_only = on; set application_name = \"agent-PROJ-1\"; select 1'",
    "kubectl -n shop-test exec orders-postgres-0 -- psql -c 'select 1'",
    "kubectl -n shop-test exec db-0 -- pg_dump app",
    "kubectl -n shop-prod exec analytics-clickhouse-0 -- clickhouse-client --readonly=2 --max_execution_time=60 -q 'select count() from bi.month'",
    "kubectl -n shop-prod exec analytics-clickhouse-0 -- clickhouse-client --readonly=0 --max_execution_time=300 --log_comment=PROJ-1230 -q 'insert into bi.cost values (1)'",
    "kubectl -n shop-prod exec cache-redis-0 -- redis-cli info memory",
    "kubectl -n shop-prod exec cache-redis-0 -- redis-cli --scan --pattern 'lock:*'",
    "kubectl -n kafka exec kafka-brokers-0 -- bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic orders.raw",
    "kubectl -n kafka exec kafka-brokers-0 -- bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group g",
    "kubectl -n secrets exec vault-0 -- vault kv get secret/prod/x",
    "kubectl -n shop-prod exec web-1 -- curl -s localhost:8080/health",
    "kubectl -n shop-prod port-forward svc/gateway 8080:80",
    "kubectl -n shop-test port-forward svc/orders-postgres 5432:5432",
    "timeout 30 kubectl -n shop-prod get pods",
    "timeout 60 kubectl -n shop-test delete pod probe",
    "for p in a b; do kubectl -n shop-test delete pod $p; done",
    "until kubectl -n shop-prod get pods | grep -q Running; do sleep 5; done",
    "echo $(kubectl -n shop-prod get pods -o name | wc -l)",
    "N=$(kubectl -n shop-prod get deploy web -o jsonpath='{.spec.replicas}')",
    "tsh kubectl -n shop-prod get pods",
    "kubectl -n shop-prod get pods -o name | xargs -n 1 kubectl -n shop-prod describe",
    "if kubectl -n shop-prod get pod web-1; then echo ok; fi",
    "echo '$(kubectl -n shop-prod delete pod x)'",
    "echo $((1 + 2))",
    "kubectl -n argocd annotate application web argocd.argoproj.io/refresh=hard --overwrite",
    "helm list -A",
    "helm -n ingress get values edge-agent",
    "helm -n shop-test upgrade --install probe ./chart",
    "helm repo update",
    "psql -h localhost -U app -c 'select 1'",
    "docker compose exec clickhouse clickhouse-client --query 'select 1'",
    "rm -rf node_modules",
    "rm -rf dist .next coverage",
    "rm -rf /tmp/build-1",
    "rm -rf ~/.cache/ms-playwright",
    "rm -rf ~/.npm/_cacache",
    "rm -f package-lock.json",
    "rm -rf $TMP/x",
    f"cd {WT} && rm -rf src/old",
    "find . -name .nx -type d -exec rm -rf {} \\;",
    "rm -rf ~/.cache/uv \\\n  ~/.cache/ms-playwright-mcp",
    "pnpm vitest run",
    "pnpm lint",
    "pnpm tsc --noEmit",
    "go test ./...",
    "pytest -q",
    "./gradlew test",
    "python3 - <<'PY'\nprint('git push --force')\nPY",
    f"{COMMIT} -F - <<'EOF'\nfix: kubectl apply docs (PROJ-1)\nEOF",
    "echo 'rm -rf ~' > notes.txt",
    "grep -rn 'tsh login' docs",
    "cat ~/.config/gitlab/token | wc -c",
    'TOKEN=$(cat ~/.config/gitlab/token); curl -s -H "PRIVATE-TOKEN: $TOKEN" https://gitlab.com/api/v4/user',
    "glab mr create --fill",
    "gh pr create --fill",
    "docker ps",
    "ls -la",
    "/usr/bin/find . -name '*.ts' | head",
    "sed -n 1,40p src/a.ts",
    "jq . package.json",
    "node scripts/build.mjs",
    "npx prettier --write src",
    "curl -s https://example.com",
    "kill 1234",
    "fuser -k 8022/tcp",
    "pgrep -af next",
    "make build",
    f"kubectl -n shop-prod exec -i db-0 -- {SAFE_PSQL} psql -c 'select count(*) from orders'",
    "kubectl -n shop-test exec -i db-0 -- psql -c '\\copy (select 1) to stdout'",
    f"kubectl -n shop-prod exec -i db-0 -- {SAFE_PSQL} sh -lc \"psql -c '\\\\copy (select * from t) to /tmp/t.csv with csv'\"",
    "kubectl -n shop-prod cp db-0:/tmp/t.csv ./t.csv",
    "pgrep -f 'exec -i db-0'",
    "kill %1",
    "cargo test",
    "mvn -q test",
    "terraform plan",
    "helm template chart",
    "git tag --list",
    "git stash list",
    "git worktree list",
    "unset HTTP_PROXY; kubectl get pods",
]


def test_dangerous_scenarios_are_denied() -> None:
    assert [name for name, command in DANGEROUS if not denied(bash(command))] == []


def test_normal_commands_pass() -> None:
    assert [c for c in NORMAL if denied(bash(c))] == []


def test_refusal_names_the_rule() -> None:
    assert reason(bash("git push --force origin main")).startswith("agent-guard (force-push): ")
    assert reason(bash("tsh login --proxy=x")).startswith("agent-guard (tsh-login): tsh login replaces")


def test_denial_of_a_bare_psql_names_what_is_missing_and_shows_the_form() -> None:
    text = reason(bash("kubectl -n shop-prod exec db-0 -- psql -c 'select 1'"))
    for needle in ("statement_timeout", "lock_timeout", "default_transaction_read_only=on", "PGAPPNAME", "PGOPTIONS="):
        assert needle in text, needle


def test_without_a_task_key_pattern_a_declared_write_passes(site) -> None:
    command = (
        "kubectl -n shop-prod exec db-0 -- env PGAPPNAME=agent-fix PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s "
        "-c default_transaction_read_only=off' psql -c 'update t set x=1'"
    )
    assert denied(bash(command))
    site({"task_keys": {"pattern": ""}})
    assert not denied(bash(command))


def test_rm_stays_inside_the_directory_the_command_started_in() -> None:
    assert denied(bash("cd .. && rm -rf shared-data", "/srv/other/repo"))
    assert not denied(bash("cd src && rm -rf generated", "/srv/other/repo"))


def test_safe_namespace_pattern_is_configurable(site) -> None:
    assert bash("kubectl -n qa delete pod x") is not None
    site({"kubernetes": {"safe_namespace_pattern": "^(qa|test)"}})
    assert not denied(bash("kubectl -n qa delete pod x"))
    assert denied(bash("kubectl -n shop-dev delete pod x"))


def test_rules_can_be_switched_off(site) -> None:
    site({"rules": {"force-push": False, "kubectl-mutation": False}})
    assert bash("git push --force origin main") is None
    assert not denied(bash("kubectl -n shop-prod delete pod web-1"))
    assert denied(bash("rm -rf ~"))


def test_russian_locale(site) -> None:
    site({"language": "ru"})
    assert "затирает" in reason(bash("git push --force origin main"))


def test_codex_gets_suggestions_instead_of_silent_rewrites(site) -> None:
    out = bash("git push -f origin agent/task-1", codex=True)
    assert denied(out) and "git push --force-with-lease origin agent/task-1" in reason(out)
    assert bash("pnpm vitest run | tail -3", codex=True) is None
    site({"codex": {"rewrites": "allow"}})
    out = bash("git push -f origin agent/task-1", codex=True)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert rewritten(out) == "git push --force-with-lease origin agent/task-1"


def test_claude_rewrites_do_not_approve_anything() -> None:
    out = bash("git push -f origin agent/task-1")
    assert "permissionDecision" not in out["hookSpecificOutput"]


def test_shared_checkout_writes_from_bash(tmp_path: Path, site) -> None:
    ws = tmp_path / "work"
    (ws / "app" / ".git").mkdir(parents=True)
    wt = ws / ".worktrees" / "app-1" / "task-1"
    wt.mkdir(parents=True)
    site({"filesystem": {"workspace_roots": [str(ws)]}})
    app = str(ws / "app")
    for command in [
        "sed -i s/a/b/ README.md",
        "cat > src/a.ts <<'EOF'\nx\nEOF",
        f"git -C {app} {COMMIT[4:]} -m x",
        "git stash",
        "git stash pop",
        "cp /tmp/x ./x",
    ]:
        assert denied(bash(command, app)), command
    for command in [
        "git status",
        "git log -5",
        "git fetch -q origin",
        "git pull --ff-only",
        "sed -n 1,5p README.md",
        "echo x > /tmp/y",
        "rm -f .git/worktrees/x/index.lock",
        "rm -rf node_modules",
        "git checkout -- .release/manifest.json",
        "chmod 600 /tmp/k",
    ]:
        assert not denied(bash(command, app)), command
    assert not denied(bash("sed -i s/a/b/ README.md", str(wt)))
    assert "git worktree add" in reason(bash("git stash", app))


def test_the_agent_cannot_rewrite_its_own_guard(tmp_path: Path) -> None:
    import os

    path = os.environ["AGENT_GUARD_CONFIG"]
    for command in (f"echo '{{}}' > {path}", f"sed -i s/true/false/ {path}", f"rm -rf {os.path.dirname(path)}", f"cp /tmp/x {path}"):
        assert reason(bash(command, str(tmp_path))).startswith("agent-guard (guard-config-write)"), command


def test_entry_point_answers_like_the_library() -> None:
    event = {"tool_name": "Bash", "cwd": WT, "tool_input": {"command": "git push --force origin main"}}
    assert denied(hook("pre-bash", event))
    assert hook("pre-bash", {"tool_name": "Bash", "cwd": WT, "tool_input": {"command": "ls"}}) is None
    assert hook("pre-bash", {"tool_name": "Read", "tool_input": {"file_path": "x"}}) is None


def test_a_broken_event_never_blocks() -> None:
    import json
    import subprocess
    import sys

    from conftest import RUN

    for payload in ("", "not json", json.dumps({"tool_name": "Bash", "tool_input": None}), json.dumps([1, 2])):
        proc = subprocess.run([sys.executable, str(RUN), "pre-bash"], input=payload, capture_output=True, text=True)
        assert proc.returncode == 0 and proc.stdout == "", payload
