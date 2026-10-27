#!/usr/bin/env bash
# Teste de restauracao: abre um backup cifrado num banco DESCARTAVEL e confere o essencial.
# Rode todo mes (um backup que nunca foi restaurado nao e backup) e antes de qualquer restore real.
#
# Uso:
#   BACKUP_AGE_IDENTITY=/caminho/chave-privada.txt \
#   RESTORE_DATABASE_URL=postgres://.../shivazen_ensaio \
#   scripts/backup/testar_restauracao.sh shivazen_<data>.dump.age
# O banco de destino tem de existir e estar vazio (createdb shivazen_ensaio). Nunca aponte para
# producao. Imprime a ultima migration e contagens; sai != 0 se algo falhar.
set -euo pipefail

arquivo="${1:?informe o arquivo .dump.age}"
: "${BACKUP_AGE_IDENTITY:?defina BACKUP_AGE_IDENTITY (chave privada age)}"
: "${RESTORE_DATABASE_URL:?defina RESTORE_DATABASE_URL (banco descartavel)}"

# decifrado num temporario privado (umask 077), apagado na saida: pg_restore le de arquivo em
# qualquer SO (no Windows ele nao le o formato custom pela entrada padrao)
umask 077
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
age -d -i "$BACKUP_AGE_IDENTITY" -o "$tmp/restauracao.dump" "$arquivo"
pg_restore --no-owner --no-acl --exit-on-error --dbname="$RESTORE_DATABASE_URL" "$tmp/restauracao.dump"
psql -v ON_ERROR_STOP=1 -At --dbname="$RESTORE_DATABASE_URL" -c "
  SELECT 'ultima_migration=' || max(name) FROM django_migrations WHERE app = 'aranha_estetica'
  UNION ALL SELECT 'clientes=' || count(*) FROM cliente
  UNION ALL SELECT 'atendimentos=' || count(*) FROM atendimento
  UNION ALL SELECT 'prontuarios=' || count(*) FROM prontuario
  UNION ALL SELECT 'trilha=' || count(*) FROM historico_alteracao
  UNION ALL SELECT 'auditoria=' || count(*) FROM log_auditoria;"
echo 'restauracao ok'
