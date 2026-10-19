
#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root; configure; require_debian

# Debian 12 默认 PostgreSQL 15。
pg_conf=/etc/postgresql/15/main/postgresql.conf

# 在安装之前判断是否已有集群，避免修改已有数据库。
new_cluster=0
[[ -e "$pg_conf" ]] || new_cluster=1

apt_install postgresql postgresql-contrib

systemctl enable --now postgresql
systemctl is-active --quiet postgresql || \
  die "PostgreSQL systemd service is inactive."

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
  log "PostgreSQL ready on 127.0.0.1:5432."
  exit 0
fi

# 仅处理本次新建的集群：
# Unix Socket 可连接，但 IPv4 无法连接。
if (( new_cluster == 1 )) && [[ -f "$pg_conf" ]] && \
   pg_isready -h /var/run/postgresql -p 5432 >/dev/null 2>&1; then

  # 确保 IPv4 回环地址存在。
  if ip -4 addr show dev lo | grep -q 'inet 127\.0\.0\.1/'; then
    log "New PostgreSQL cluster responds over Unix socket but not IPv4."
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
      if pg_ctlcluster 15 main restart && wait_ipv4_postgres; then
        rm -f -- "$backup"
        log "PostgreSQL ready on IPv4 after configuring the new cluster."
        exit 0
      fi
    fi

    # 修改失败则恢复配置，避免将数据库留在错误配置下。
    log "WARNING: IPv4 configuration failed; restoring original configuration."
    cp -p -- "$backup" "$pg_conf"
    rm -f -- "$backup"
    pg_ctlcluster 15 main restart || \
      pg_ctlcluster 15 main start || true
  fi
fi

# 统一输出诊断，供用户排查。
log "PostgreSQL diagnostics:"
pg_lsclusters || true
ss -lntp '( sport = :5432 )' || true
journalctl -u postgresql@15-main -n 25 --no-pager || true

die "PostgreSQL is not accepting IPv4 connections on 127.0.0.1:5432; existing cluster settings were preserved."
