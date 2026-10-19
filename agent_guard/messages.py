"""Refusal and advice texts. English is the default; set ``language = "ru"`` for Russian.

Every text is written for the agent, not for a human: it says what went wrong and what to
run instead, so the next step of the agent is the right one.
"""

from __future__ import annotations

EN = {
    "prefix": "agent-guard ({rule}): ",
    "pkill": (
        "`pkill -f` matches its pattern against whole command lines, the agent's own shell included, and kills it (exit 144). "
        "Stop the process by PID: `pgrep -af '<pattern>'`, then `kill <pid>`; or by port: `fuser -k <port>/tcp`."
    ),
    "force_push": (
        "`git push --force` overwrites whatever is on the remote, other people's commits included. "
        "After a rebase push with `--force-with-lease`; never force-push to {protected}."
    ),
    "force_protected": "Force-pushing to a protected branch is refused even with --force-with-lease. Open a merge or pull request.",
    "run_instead": "Run this instead:\n{command}",
    "where_ns": "namespace {ns}",
    "where_default_ns": "the default namespace",
    "kubectl": "`kubectl {verb}` in {where} bypasses GitOps. {hint}",
    "kube_migration": (
        "A migration or data change through `kubectl exec` in {where}: the agent does not migrate production by hand. "
        "Schema changes ship with the release (an init container or a migration job)."
    ),
    "kube_copy_stdout": (
        "Streaming a table to stdout through `kubectl exec` in {where}: if the stream breaks, psql stays alive in the pod "
        "and keeps its table lock for hours or days. Write to a file inside the pod, "
        "`\\copy (…) to '/tmp/<name>.csv' with csv`, then fetch it: `kubectl cp <pod>:/tmp/<name>.csv .`."
    ),
    "psql_missing_statement": "statement_timeout between 1 ms and {max} s",
    "psql_missing_lock": "lock_timeout between 1 ms and {max} s",
    "psql_missing_ro": "default_transaction_read_only=on",
    "psql_missing_name": "PGAPPNAME (whose session this is)",
    "psql_envelope": (
        "psql in a production database ({where}) without server-side limits: missing {missing}. "
        "A session whose client is gone stays alive in the pod and keeps holding locks. "
        "Form: kubectl exec <pod> -- {envelope}"
    ),
    "psql_write": (
        "A write to a production database ({where}) without a task key. Production data changes with a release migration; "
        "if a manual change is really needed, declare it: default_transaction_read_only=off and the task key in PGAPPNAME (agent-<task>)."
    ),
    "ch_missing_time": "--max_execution_time between 1 and {max}",
    "ch_missing_ro": "--readonly=2",
    "ch_envelope": (
        "clickhouse-client in a production database ({where}) without limits: missing {missing}. "
        "A heavy query without a time ceiling takes the CPU from everything else. Form: {envelope}"
    ),
    "ch_write": "A write to a production ClickHouse ({where}) without a task key: --readonly=0 is declared together with the task key (e.g. --log_comment=<task>).",
    "pg_dump": "pg_dump from a production database ({where}) through kubectl exec: a long dump holds a snapshot and dies with its stream. {hint}",
    "redis": "Dangerous redis-cli command in {where}: FLUSH*, KEYS, SHUTDOWN, CONFIG and DEBUG on a production Redis stop the service or wipe data. Iterate keys with SCAN.",
    "kafka": (
        "Mutating Kafka command in {where}: deleting topics or records, resetting or deleting offsets and moving partitions "
        "go through your infrastructure repository, not by hand."
    ),
    "port_forward_db": (
        "Port-forward to a production database in {where}: behind it a client talks to the database with no limits and no trace. "
        "Run the query inside the pod: kubectl exec <pod> -- {envelope}"
    ),
    "helm": "`helm {verb}` in {where}: a release installed by hand is invisible to GitOps and cannot be reproduced from the repository. {hint}",
    "kill_kubectl_exec": (
        "Killing a local `kubectl exec` does not kill the psql inside the pod: it stays alive, holds its table lock and breaks the next migration. "
        "End the server-side session first: `kubectl exec <pod> -- env PGAPPNAME=agent-<task> "
        "PGOPTIONS='-c statement_timeout=30s -c lock_timeout=3s -c default_transaction_read_only=on' "
        "psql -Atc \"select pg_terminate_backend(pid) from pg_stat_activity where (application_name='psql' "
        "or application_name like 'agent-%') and pid<>pg_backend_pid()\"`, then kill."
    ),
    "rm": (
        "`rm -r` outside scratch space and worktrees: {targets}. Recursive deletes are allowed in {scratch}, inside a worktree "
        "(`{worktree_dir}/…`), in caches, in build output and below the directory the command started in; ask a human for anything else."
    ),
    "branch_local": "An agent branch is not force-deleted without proof that it was merged. `git branch -d` refuses an unmerged branch by itself.",
    "branch_remote": "Deleting an agent branch on the remote is refused: until it is merged it is the only copy of the work.",
    "shared_checkout": (
        "{repo} is a shared checkout: people and other agents work in it, so it is changed neither by edit tools nor from the shell. "
        "Create a worktree and work in it: `{hint}`."
    ),
    "literal_token": (
        "The command contains a literal token: it stays in the transcript and can end up in a file or a log. "
        "Read it into a variable instead: TOKEN=$(cat ~/.config/<tool>/token)."
    ),
    "secret_file": "{name} is a secrets file; the agent does not edit it. Secrets live in {where}; document the variables in .env.example.",
    "token_in_file": "A real token is about to be written into a file. Keep the secret in {where}; the code refers to it by variable name only.",
    "guard_config": "{path} is agent-guard's own configuration. The agent does not change the rules that guard it; ask a human.",
    "exit_code": "[exit code: {code}]",
    "aftercare": (
        "The kubectl exec stream into a production database broke or went to the background: the psql inside the pod may still be alive "
        "and holding a table lock. Check for the orphan and end it now: "
        "`kubectl -n <ns> exec <pod> -c postgres -- env PGAPPNAME=agent-<task> "
        "PGOPTIONS='-c statement_timeout=30s -c lock_timeout=3s -c default_transaction_read_only=on' "
        'sh -c \'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pid, application_name, state, left(query,60) '
        "from pg_stat_activity where (application_name=$$psql$$ or application_name like $$agent-%$$) and pid<>pg_backend_pid()\"'`, "
        "then `select pg_terminate_backend(<pid>)` in the same shell."
    ),
    # background leases
    "bg_daemon": "daemon",
    "bg_kept": "kept: {why}",
    "bg_default": "default {hours:g} h",
    "bg_no_limit": "no limit",
    "bg_expired": "expired",
    "bg_hours": "{n:.1f} h",
    "bg_days": "{n:.1f} d",
    "bg_where": "pid {pid}, {age}, cwd {cwd}: `{cmd}`",
    "bg_warn": (
        "In {left} agent-guard will stop your background process: {where} ({source}). "
        "If it must live longer: `agent-bg keep {pid} --hours N --why '…'`; if it is not needed, stop it yourself."
    ),
    "bg_stopped": (
        "agent-guard stopped your background process: {where}. Its lifetime as a detached process ran out ({source}). "
        "If it is still needed, start it again with `{ttl_env}=<hours>` or `{keep_env}=<reason>` in its environment."
    ),
    "bg_stopped_tag": "agent-guard stopped",
    "bg_system": "agent-guard stopped {n} background process(es) of this session (lifetime ran out).",
    "bg_hint": (
        "A detached process (nohup, setsid, `&`) outlives this call but lives {ttl:g} h from its start; then agent-guard stops it "
        "and tells you. To keep it longer, set the lifetime at start (`{ttl_env}=<hours>` or `{keep_env}=<reason>` in the command's environment) "
        "or extend a running one: `agent-bg keep <pid> --hours N --why '…'`. Stop it yourself when you are done; "
        "list your detached processes with `agent-bg list`."
    ),
    "bg_report_warned": "warned {where} ({source})",
    "bg_report_stopped": "stopped {where} ({source})",
    "bg_no_process": "no process {pid}",
    "bg_need_reason": "a reason is required: --why '…' (a week from now it tells why this is still running)",
}

RU = {
    "prefix": "agent-guard ({rule}): ",
    "pkill": (
        "pkill -f ищет шаблон по всей командной строке, в том числе в оболочке самого агента, и убивает её (код 144). "
        "Останови процесс по PID: `pgrep -af '<шаблон>'`, затем `kill <pid>`; или по порту: `fuser -k <порт>/tcp`."
    ),
    "force_push": (
        "git push --force затирает то, что лежит на сервере, вместе с чужими коммитами. После ребейза пушь с --force-with-lease; в {protected} — никогда."
    ),
    "force_protected": "Принудительный пуш в защищённую ветку запрещён даже с --force-with-lease. Открой merge или pull request.",
    "run_instead": "Выполни вместо этого:\n{command}",
    "where_ns": "неймспейсе {ns}",
    "where_default_ns": "неймспейсе по умолчанию",
    "kubectl": "kubectl {verb} в {where} идёт мимо GitOps. {hint}",
    "kube_migration": (
        "Миграция или изменение данных через kubectl exec в {where}: агент не мигрирует прод руками. "
        "Изменения схемы едут с выпуском (init-контейнер или задание миграции)."
    ),
    "kube_copy_stdout": (
        "Выгрузка в stdout через kubectl exec в {where}: при обрыве потока psql в поде остаётся жить и держит блокировку таблицы "
        "часами и сутками. Пиши в файл внутри пода, `\\copy (…) to '/tmp/<имя>.csv' with csv`, потом `kubectl cp <pod>:/tmp/<имя>.csv .`."
    ),
    "psql_missing_statement": "statement_timeout от 1 мс до {max} с",
    "psql_missing_lock": "lock_timeout от 1 мс до {max} с",
    "psql_missing_ro": "default_transaction_read_only=on",
    "psql_missing_name": "PGAPPNAME (чья это сессия)",
    "psql_envelope": (
        "psql в боевой базе ({where}) без пределов на стороне сервера: не хватает {missing}. "
        "Сессия, потерявшая клиента, остаётся жить в поде и держит блокировки. Форма: kubectl exec <pod> -- {envelope}"
    ),
    "psql_write": (
        "Запись в боевую базу ({where}) без номера задачи. Данные прода меняются миграцией выпуска; если правка руками действительно нужна, "
        "её объявляют: default_transaction_read_only=off и номер задачи в PGAPPNAME (agent-<задача>)."
    ),
    "ch_missing_time": "--max_execution_time от 1 до {max}",
    "ch_missing_ro": "--readonly=2",
    "ch_envelope": (
        "clickhouse-client в боевой базе ({where}) без пределов: не хватает {missing}. "
        "Тяжёлый запрос без потолка времени отбирает процессор у всего остального. Форма: {envelope}"
    ),
    "ch_write": "Запись в боевой ClickHouse ({where}) без номера задачи: --readonly=0 объявляется вместе с номером задачи (например, --log_comment=<задача>).",
    "pg_dump": "pg_dump из боевой базы ({where}) через kubectl exec: долгий дамп держит снимок и умирает вместе с потоком. {hint}",
    "redis": "Опасная команда redis-cli в {where}: FLUSH*, KEYS, SHUTDOWN, CONFIG и DEBUG на боевом Redis останавливают сервис или стирают данные. Ключи перебирают через SCAN.",
    "kafka": (
        "Изменяющая команда Kafka в {where}: удаление топиков и записей, сброс и удаление смещений, перенос партиций "
        "делаются через репозиторий инфраструктуры, не руками."
    ),
    "port_forward_db": (
        "Проброс порта к боевой базе в {where}: за пробросом клиент ходит в базу без пределов и без следа. "
        "Запрос выполняется внутри пода: kubectl exec <pod> -- {envelope}"
    ),
    "helm": "helm {verb} в {where}: релиз, поставленный руками, не видит GitOps и он не воспроизводится из репозитория. {hint}",
    "kill_kubectl_exec": (
        "Убитый локально kubectl exec не убивает psql в поде: тот остаётся жить, держит блокировку таблицы и валит следующую миграцию. "
        "Сначала сними серверную сессию: `kubectl exec <pod> -- env PGAPPNAME=agent-<задача> "
        "PGOPTIONS='-c statement_timeout=30s -c lock_timeout=3s -c default_transaction_read_only=on' "
        "psql -Atc \"select pg_terminate_backend(pid) from pg_stat_activity where (application_name='psql' "
        "or application_name like 'agent-%') and pid<>pg_backend_pid()\"`, потом kill."
    ),
    "rm": (
        "rm -r вне временных каталогов и worktree: {targets}. Рекурсивно удалять можно в {scratch}, внутри worktree (`{worktree_dir}/…`), "
        "в кэшах, в результатах сборки и ниже каталога, где команда началась; остальное — попроси человека."
    ),
    "branch_local": "Ветку агента не удаляют принудительно без доказательства слияния. `git branch -d` сам откажет, если она не слита.",
    "branch_remote": "Удалять ветку агента на сервере нельзя: до слияния это единственная копия работы.",
    "shared_checkout": (
        "{repo} — общий чекаут: в нём работают люди и другие агенты, поэтому его не правят ни инструментами, ни из оболочки. "
        "Создай worktree и работай в нём: `{hint}`."
    ),
    "literal_token": (
        "В команде токен литералом: он останется в транскрипте и может уйти в файл или лог. Читай его в переменную: TOKEN=$(cat ~/.config/<tool>/token)."
    ),
    "secret_file": "{name} — файл с секретами, агент его не правит. Секреты живут в {where}; переменные описывают в .env.example.",
    "token_in_file": "В файл уходит настоящий токен. Секрет хранится в {where}, в коде — только имя переменной.",
    "guard_config": "{path} — конфигурация самого agent-guard. Агент не меняет правила, которые его охраняют; попроси человека.",
    "exit_code": "[код выхода: {code}]",
    "aftercare": (
        "Поток kubectl exec к боевой базе оборвался или ушёл в фон: psql внутри пода мог остаться жить и держать блокировку таблицы. "
        "Проверь и сними сироту прямо сейчас: "
        "`kubectl -n <ns> exec <pod> -c postgres -- env PGAPPNAME=agent-<task> "
        "PGOPTIONS='-c statement_timeout=30s -c lock_timeout=3s -c default_transaction_read_only=on' "
        'sh -c \'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select pid, application_name, state, left(query,60) '
        "from pg_stat_activity where (application_name=$$psql$$ or application_name like $$agent-%$$) and pid<>pg_backend_pid()\"'`, "
        "затем `select pg_terminate_backend(<pid>)` в той же оболочке."
    ),
    "bg_daemon": "демон",
    "bg_kept": "продлён: {why}",
    "bg_default": "по умолчанию {hours:g} ч",
    "bg_no_limit": "без срока",
    "bg_expired": "истёк",
    "bg_hours": "{n:.1f} ч",
    "bg_days": "{n:.1f} сут",
    "bg_where": "pid {pid}, {age}, cwd {cwd}: `{cmd}`",
    "bg_warn": (
        "Через {left} agent-guard остановит твой фоновый процесс: {where} ({source}). "
        "Должен жить дольше — `agent-bg keep {pid} --hours N --why '…'`; не нужен — останови сам."
    ),
    "bg_stopped": (
        "agent-guard остановил твой фоновый процесс: {where}. Срок жизни отвязанного процесса истёк ({source}). "
        "Если он ещё нужен, запусти заново с `{ttl_env}=<часы>` или `{keep_env}=<причина>` в окружении."
    ),
    "bg_stopped_tag": "agent-guard остановил",
    "bg_system": "agent-guard остановил фоновых процессов этой сессии: {n} (срок жизни истёк).",
    "bg_hint": (
        "Отвязанный процесс (nohup, setsid, `&`) переживает этот вызов, но живёт {ttl:g} ч от старта: дальше agent-guard его остановит "
        "и скажет об этом. Нужно дольше — задай срок при запуске (`{ttl_env}=<часы>` или `{keep_env}=<причина>` в окружении команды) "
        "или продли живой: `agent-bg keep <pid> --hours N --why '…'`. Не нужен — останови сам; свои отвязанные процессы — `agent-bg list`."
    ),
    "bg_report_warned": "предупреждён {where} ({source})",
    "bg_report_stopped": "остановлен {where} ({source})",
    "bg_no_process": "нет процесса {pid}",
    "bg_need_reason": "нужна причина: --why '…' — через неделю по ней поймут, зачем это живёт",
}

CATALOGS = {"en": EN, "ru": RU}


def t(cfg: dict, key: str, **kwargs: object) -> str:
    catalog = CATALOGS.get(str(cfg.get("language") or "en"), EN)
    return catalog.get(key, EN[key]).format(**kwargs)
