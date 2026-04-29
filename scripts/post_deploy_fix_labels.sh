#!/bin/bash
# Pos-deploy: realinha labels DB com novo nome aranha_estetica.
# Rodar UMA VEZ apos primeiro deploy do codigo c/ aranha_estetica subir verde.
#
# Uso:
#   export RAILWAY_API_TOKEN="<seu_token>"
#   bash scripts/post_deploy_fix_labels.sh
#
# Idempotente: pode rodar multiplas vezes sem efeito colateral.

set -e

if [ -z "$RAILWAY_API_TOKEN" ]; then
  echo "ERRO: RAILWAY_API_TOKEN nao setado"
  exit 1
fi

DB_URL=$(railway variables --service Postgres --environment production --kv 2>/dev/null \
  | grep "^DATABASE_PUBLIC_URL=" | cut -d= -f2-)

if [ -z "$DB_URL" ]; then
  echo "ERRO: DATABASE_PUBLIC_URL nao encontrado"
  exit 1
fi

python3 << PYEOF
import psycopg2
DB = "$DB_URL"
conn = psycopg2.connect(DB); conn.autocommit = True
c = conn.cursor()
c.execute("UPDATE django_migrations SET app='aranha_estetica' WHERE app='app_shivazen'")
print('mig updated:', c.rowcount)
c.execute("UPDATE django_content_type SET app_label='aranha_estetica' WHERE app_label='app_shivazen'")
print('ct updated:', c.rowcount)
conn.close()
PYEOF
echo "OK — labels alinhados. Reinicie service web no Railway p/ Django re-validar."
