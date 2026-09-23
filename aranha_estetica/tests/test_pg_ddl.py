"""DDL so-Postgres das migrations: existe e barra o que promete.

O SQLite nao tem nenhum destes objetos, e sem estes testes a suite passava
inteira (853/853) com todos removidos — um squash/AlterField que perdesse o
EXCLUDE anti-double-booking ou o trigger do ledger entraria verde no CI.

No SQLite a classe inteira e pulada. No CI (job postgres) PG_EXIGE_ICU=1 torna
obrigatoria a collation pt_br (0038 so avisa e segue quando o build nao tem ICU).
"""
import os
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase

from aranha_estetica.models import Atendimento, FormularioAnamnese, Prontuario, RespostaAnamnese
from aranha_estetica.models.extras import Carteira, MovimentoCarteira
from aranha_estetica.tests.factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)

PG = connection.vendor == 'postgresql'

# conname -> migration que cria
CONSTRAINTS_SO_PG = {
    'excl_atendimento_sobreposicao': '0035',
    'chk_cliente_telefone_digits': '0034',
    'chk_cliente_cpf_digits': '0034',
    'chk_prontuario_extras_objeto': '0043',
    'chk_resposta_anamnese_objeto': '0043',
    'chk_formulario_schema_lista': '0043',
}
TRIGGERS_SO_PG = {'trg_movimento_carteira_imutavel': '0038'}


@skipUnless(PG, 'DDL so-Postgres (EXCLUDE, CHECK regex/jsonb, trigger, collation)')
class DDLSoPostgresTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.at = criar_atendimento(self.cli, self.prof, self.proc)

    def _sql(self, sql, params=None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_objetos_existem(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT conname FROM pg_constraint WHERE conname = ANY(%s)',
                [list(CONSTRAINTS_SO_PG)],
            )
            constraints = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                'SELECT tgname FROM pg_trigger WHERE tgname = ANY(%s) AND NOT tgisinternal',
                [list(TRIGGERS_SO_PG)],
            )
            triggers = {row[0] for row in cursor.fetchall()}
        self.assertEqual(constraints, set(CONSTRAINTS_SO_PG))
        self.assertEqual(triggers, set(TRIGGERS_SO_PG))

    def _outro(self, prof=None, status='PENDENTE', delta=10):
        return Atendimento.objects.create(
            cliente=criar_cliente(), profissional=prof or self.prof, procedimento=self.proc,
            data_hora_inicio=self.at.data_hora_inicio + timedelta(minutes=delta),
            data_hora_fim=self.at.data_hora_fim + timedelta(minutes=delta), status=status,
        )

    def test_exclude_barra_sobreposicao_do_mesmo_profissional(self):
        # DEFERRABLE INITIALLY IMMEDIATE: falha no proprio INSERT
        for status in ('PENDENTE', 'AGENDADO', 'CONFIRMADO'):
            with self.subTest(status=status), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                self._outro(status=status)
        self._outro(status='CANCELADO')                     # inativo nao conflita
        self._outro(prof=criar_profissional('Dra. Bia'))    # outro profissional
        self._outro(delta=self.proc.duracao_minutos)        # tstzrange [) contiguo

    def test_ledger_imutavel(self):
        carteira = Carteira.objects.create(cliente=self.cli, saldo=Decimal('10'))
        mov = MovimentoCarteira.objects.create(
            carteira=carteira, tipo='CREDITO', origem='OUTRO',
            valor=Decimal('10'), saldo_resultante=Decimal('10'),
        )
        # RAISE do plpgsql (P0001) -> InternalError, subclasse de DatabaseError
        with self.assertRaises(DatabaseError), transaction.atomic():
            MovimentoCarteira.objects.filter(pk=mov.pk).update(valor=Decimal('99'))
        with self.assertRaises(DatabaseError), transaction.atomic():
            self._sql('DELETE FROM movimento_carteira WHERE id = %s', [mov.pk])
        mov.refresh_from_db()
        self.assertEqual(mov.valor, Decimal('10'))

    def test_checks_regex_cliente(self):
        for coluna, valor in (('telefone', 'abc'), ('telefone', '123'),
                              ('cpf', '123456789ab'), ('cpf', '1234')):
            with self.subTest(coluna=coluna, valor=valor), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                self._sql(f'UPDATE cliente SET {coluna} = %s WHERE id = %s', [valor, self.cli.pk])
        # formato valido segue aceito
        self._sql('UPDATE cliente SET cpf = %s WHERE id = %s', ['12345678901', self.cli.pk])

    def test_checks_jsonb(self):
        # NOT VALID: nao revalida o legado, mas vale p/ toda escrita nova
        prontuario = Prontuario.objects.create(cliente=self.cli)
        formulario = FormularioAnamnese.objects.create(nome='Ficha', schema_json=[])
        resposta = RespostaAnamnese.objects.create(
            formulario=formulario, cliente=self.cli, respostas_json={},
        )
        casos = (
            ("UPDATE prontuario SET respostas_extras = '[]'::jsonb WHERE id = %s", prontuario.pk),
            ("UPDATE resposta_anamnese SET respostas_json = '[]'::jsonb WHERE id = %s", resposta.pk),
            ("UPDATE formulario_anamnese SET schema_json = '{}'::jsonb WHERE id = %s", formulario.pk),
        )
        for sql, pk in casos:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [pk])

    def test_collation_nome(self):
        if os.environ.get('PG_EXIGE_ICU') != '1':
            # Sem ICU a 0038 so avisa. Nao usar to_regcollation('pt_br') como
            # gatilho: se o passo sumisse, o teste pularia em vez de reprovar.
            self.skipTest('collation pt_br so e exigida com PG_EXIGE_ICU=1 (CI)')
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, collation_name FROM information_schema.columns "
                "WHERE column_name = 'nome' AND table_name IN ('cliente', 'profissional', 'procedimento')"
            )
            collations = dict(cursor.fetchall())
        self.assertEqual(
            collations, {'cliente': 'pt_br', 'profissional': 'pt_br', 'procedimento': 'pt_br'},
        )
