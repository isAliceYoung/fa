
#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian

# Debian 12 默认 PostgreSQL 15。
pg_conf=/etc/postgresql/15/main/postgresql.conf
pg_pending=/var/lib/webdesktop-deploy/postgresql-15-main-initializing

# 在安装之前判断是否已有集群，避免修改已有数据库。
[[ ! -L "$pg_pending" && ( ! -e "$pg_pending" || -f "$pg_pending" ) ]] ||
  die "Invalid PostgreSQL initialization marker; inspect $pg_pending manually."
# Keep this marker through failures so later runs can finish initialization.
if [[ ! -e "$pg_conf" && ! -L "$pg_conf" ]]; then
  install -d -m 700 /var/lib/webdesktop-deploy
  (umask 077; : > "$pg_pending")
fi

apt_install postgresql postgresql-contrib

if ! systemctl enable --now postgresql; then
  log "WARNING: PostgreSQL service startup reported a failure; checking the actual cluster IPv4 endpoint before recovery."
fi

# 等待 IPv4 数据库连接就绪。
wait_ipv4_postgres() {
  local attempt
  for ((attempt=1; attempt<=10; attempt++)); do
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# 正常环境不修改 PostgreSQL 配置。
if wait_ipv4_postgres; then
  # Readiness, not the umbrella service state, completes initialization.
  [[ ! -f "$pg_pending" ]] || rm -f -- "$pg_pending"
  log "PostgreSQL ready on 127.0.0.1:5432."
  exit 0
fi

# 仅恢复安装器持有初始化标记的集群，不要求 Unix Socket 已经可连接。
if [[ -f "$pg_pending" && -f "$pg_conf" && ! -L "$pg_conf" ]]; then

  # 确保 IPv4 回环地址存在。
  if ip -4 addr show dev lo | grep -q 'inet 127\.0\.0\.1/'; then
    log "Initializing PostgreSQL cluster is not accepting IPv4 connections."
    log "Configuring PostgreSQL IPv4 loopback."

    backup="$(mktemp "${pg_conf}.webdesktop.XXXXXX")"
    cp -p -- "$pg_conf" "$backup"

    # 直接修改配置，避免 pg_conftool 的字符串引号问题。
    if python3 - "$pg_conf" <<'PY'
import pathlib
import re
import sys

conf = pathlib.Path(sys.argv[1])
content = conf.read_text()

pattern = re.compile(
    r'^[ \t]*#?[ \t]*listen_addresses[ \t]*=[^\n]*$',
    re.MULTILINE,
)

lines = pattern.findall(content)

if len(lines) != 1:
    raise SystemExit(
        "Expected exactly one listen_addresses entry; "
        "refusing automatic change."
    )

value = lines[0].split('=', 1)[1].split('#', 1)[0].strip()

# Resume a run interrupted after this exact IPv4 setting was written, without
# rewriting configuration. All other custom addresses are still refused.
if value in ('127.0.0.1', "'127.0.0.1'", '"127.0.0.1"'):
    raise SystemExit(0)

if value not in ('localhost', "'localhost'", '"localhost"'):
    raise SystemExit(
        "listen_addresses is custom; refusing automatic change."
    )

conf.write_text(
    pattern.sub(
        "listen_addresses = '127.0.0.1'",
        content,
        count=1,
    )
)
PY
    then
      was_running=0
      if pg_ctlcluster 15 main status >/dev/null 2>&1; then
        was_running=1
        cluster_action=restart
      else
        cluster_action=start
      fi
      log "Attempting PostgreSQL 15/main $cluster_action."
      if pg_ctlcluster 15 main "$cluster_action" && wait_ipv4_postgres; then
        rm -f -- "$backup" "$pg_pending"
        log "PostgreSQL ready on IPv4 after configuring the new cluster."
        exit 0
      fi
      log "WARNING: IPv4 startup failed; restoring original configuration and retaining the initialization marker."
      cp -p -- "$backup" "$pg_conf"
      if (( was_running == 1 )); then
        pg_ctlcluster 15 main restart || true
      fi
    else
      log "WARNING: Automatic PostgreSQL configuration refused; settings and initialization marker were preserved."
    fi
    rm -f -- "$backup"
  fi
fi

# 统一输出诊断，供用户排查。
log "PostgreSQL diagnostics:"
pg_lsclusters || true
ss -lntp '( sport = :5432 )' || true
journalctl -u postgresql@15-main -n 25 --no-pager || true

die "PostgreSQL is not accepting IPv4 connections on 127.0.0.1:5432; existing cluster settings were preserved."
