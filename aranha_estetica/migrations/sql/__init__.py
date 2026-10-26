"""Texto SQL das rotinas do banco, versionado por migration (docs/specs/padrao-sql.md §5).

Cada modulo ``vNNNN_*.py`` e importado SO pela migration NNNN e fica CONGELADO
depois de aplicado: mudar uma funcao/view/procedure = modulo novo + migration nova
com ``CREATE OR REPLACE`` (a migration antiga continua recriando o texto antigo no
banco do zero, e a nova o substitui).

O pacote nao e uma migration: o MigrationLoader do Django so carrega modulos (nao
pacotes) do diretorio ``migrations``.

Execucao: sempre ``schema_editor.execute(sql, None)`` — com ``params=()`` o
psycopg2 faz *mogrify* e le o ``%`` do ``RAISE``/``LIKE`` como placeholder.
"""
