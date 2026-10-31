#!/usr/bin/env bash
# Backup cifrado do Postgres de producao, fora do provedor (docs/PROJECT.md §14 h).
#
#   pg_dump -Fc | age -r <chave publica>  ->  jaqueline-aranha-estetica_<data>.dump.age  [-> rclone p/ o bucket]
#
# So a chave PUBLICA (age1...) fica onde o backup roda; a privada fica offline com a dona (sem
# ela o backup nao abre — e quem rouba o bucket tambem nao abre). Nunca grave o dump em claro:
# ele tem cadastro, agenda e (cifrado pela app) o prontuario.
#
# Uso:
#   DATABASE_URL=postgres://... BACKUP_AGE_RECIPIENTS="age1... age1..." \
#   [BACKUP_DIR=/var/backups/jaqueline-aranha-estetica] [BACKUP_RCLONE_DESTINO=r2:jaqueline-aranha-estetica-backups/diario] \
#   scripts/backup/backup_postgres.sh
# Requer pg_dump da mesma versao major do servidor (18) ou mais nova, age e (opcional) rclone.
# Imprime o caminho do arquivo gerado. Sai != 0 em qualquer falha (pipefail).
set -euo pipefail

: "${DATABASE_URL:?defina DATABASE_URL}"
: "${BACKUP_AGE_RECIPIENTS:?defina BACKUP_AGE_RECIPIENTS (chave publica age; a privada fica offline)}"
dir="${BACKUP_DIR:-$HOME/backups-jaqueline-aranha-estetica}"   # fora do repositorio por padrao
mkdir -p "$dir"
umask 077

destinatarios=()
for r in $BACKUP_AGE_RECIPIENTS; do
  destinatarios+=(-r "$r")
done

arquivo="$dir/jaqueline-aranha-estetica_$(date -u +%Y%m%dT%H%M%SZ).dump.age"
pg_dump --format=custom --no-owner --no-acl --dbname="$DATABASE_URL" | age "${destinatarios[@]}" -o "$arquivo"
test -s "$arquivo"

if [ -n "${BACKUP_RCLONE_DESTINO:-}" ]; then
  rclone copy "$arquivo" "$BACKUP_RCLONE_DESTINO"
fi
echo "$arquivo"
