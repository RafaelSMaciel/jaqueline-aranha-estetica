"""Paridade SQL x Python — so Postgres (docs/specs/padrao-sql.md §5.8).

Leitura (0057): cada funcao fn_* / view vw_* e comparada com a implementacao Python que o app
usa (SlotService, AgendaExcecao.ocorrencias, CompraPacote.saldo_por_procedimento, utils/nps,
creditos_com_status/ComissaoService, services/consentimentos, services/financeiro,
LgpdService.candidatos_purga) em cenarios representativos.

Escrita (0058): a procedure e o fallback Python rodam sobre a MESMA fixture, cada um numa
savepoint desfeita depois da "foto" normalizada (ids novos e instantes fora) — mesmo efeito
nos dois lados. O fallback roda no Postgres com FORCAR_LOGICA_PYTHON (so para este teste).

Regra de manutencao: mudou a regra no Python -> este teste quebra no CI Postgres ate a
funcao/procedure ser atualizada por uma migration nova (CREATE OR REPLACE).
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from aranha_estetica.models import (
    AgendaExcecao, AgendaHorario, Atendimento, AvaliacaoNPS, Carteira, Cliente, CodigoOtp, CompraPacote,
    CompraPacoteItem, Consentimento, ConsumoSessao, FormularioAnamnese, ItemPacote, LinkAcesso, ListaEspera, LogAuditoria,
    MovimentoComissao, Notificacao, Pacote, Procedimento, Profissional, Prontuario, ProntuarioVersao,
    RespostaAnamnese, Usuario,
)
from aranha_estetica.services import consentimentos, financeiro
from aranha_estetica.services.comissao_service import ComissaoService
from aranha_estetica.services.disponibilidade import SlotService
from aranha_estetica.services.lgpd import LgpdService
from aranha_estetica.utils.nps import resumo_nps

from .factories import (
    conceder, criar_atendimento, criar_cliente, criar_comissao, criar_compra_pacote, criar_link,
    criar_notificacao_com_link,
)

PG = connection.vendor == 'postgresql'
TZ = ZoneInfo(settings.TIME_ZONE)
AGORA = datetime(2026, 11, 3, 10, 17, tzinfo=TZ)  # terca-feira, 10:17 local (instante fixo dos dois lados)
FORCAR_PYTHON = override_settings(FORCAR_LOGICA_PYTHON=True)


def _q(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _exec(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def _local(dia, hora, minuto=0):
    return datetime.combine(dia, time(hora, minuto), tzinfo=TZ)


def _ordenado(linhas):
    return sorted(linhas, key=repr)


def _rodar_e_fotografar(funcao, foto):
    """Roda ``funcao`` numa savepoint, fotografa o banco e desfaz: a outra implementacao parte
    exatamente da mesma fixture."""
    with transaction.atomic():
        resultado = funcao()
        imagem = foto()
        transaction.set_rollback(True)
    return resultado, imagem


# ═════════════════════════════════════════════════════════════════════════════
# Agenda: fn_horarios_livres x SlotService, fn_ocorrencias_recorrencia x AgendaExcecao.ocorrencias
# ═════════════════════════════════════════════════════════════════════════════
@skipUnless(PG, 'funcoes de leitura so existem no Postgres (0057)')
class ParidadeAgendaTests(TestCase):
    DIAS = range(-1, 66)  # passado, dentro e alem da antecedencia maxima

    @classmethod
    def setUpTestData(cls):
        hoje = AGORA.date()
        # P1: duas janelas/dia (seg-sab), antecedencia minima 2h, maxima 30 dias
        cls.p1 = Profissional.objects.create(nome='Dra. Duas Janelas', min_notice_horas=2, max_advance_dias=30)
        for dia in range(1, 7):
            AgendaHorario.objects.create(profissional=cls.p1, dia_semana=dia, hora_inicio=time(8), hora_fim=time(12))
            AgendaHorario.objects.create(profissional=cls.p1, dia_semana=dia, hora_inicio=time(13),
                                         hora_fim=time(18))
        # P2: uma janela (seg-sex ate 17:30, sab manha), sem antecedencia minima, maxima 60 dias
        cls.p2 = Profissional.objects.create(nome='Dra. Uma Janela', min_notice_horas=0, max_advance_dias=60)
        for dia in range(1, 6):
            AgendaHorario.objects.create(profissional=cls.p2, dia_semana=dia, hora_inicio=time(9),
                                         hora_fim=time(17, 30))
        AgendaHorario.objects.create(profissional=cls.p2, dia_semana=6, hora_inicio=time(9), hora_fim=time(12))
        # procedimentos: sem intervalo, com intervalo, duracao "quebrada"
        cls.proc_a = Procedimento.objects.create(nome='A 30', duracao_minutos=30, buffer_minutos=0)
        cls.proc_b = Procedimento.objects.create(nome='B 60+15', duracao_minutos=60, buffer_minutos=15)
        cls.proc_c = Procedimento.objects.create(nome='C 45+10', duracao_minutos=45, buffer_minutos=10)
        cli = Cliente.objects.create(nome='Cliente Agenda', telefone='17988880000')

        def excecao(**campos):
            return AgendaExcecao.objects.create(**campos)

        # folga em intervalo (P1), recesso global ativo, feriado global INATIVO, horario diferente (P2)
        excecao(profissional=cls.p1, tipo='FOLGA', data_inicio=hoje + timedelta(5), data_fim=hoje + timedelta(7))
        excecao(tipo='FOLGA', origem='RECESSO', data_inicio=hoje + timedelta(10), data_fim=hoje + timedelta(10))
        excecao(tipo='FOLGA', origem='FERIADO_LOCAL', data_inicio=hoje + timedelta(11),
                data_fim=hoje + timedelta(11), ativo=False)
        excecao(profissional=cls.p2, tipo='HORARIO_DIFERENTE', data_inicio=hoje + timedelta(12),
                data_fim=hoje + timedelta(12), hora_inicio=time(10), hora_fim=time(14))
        excecao(profissional=cls.p2, tipo='HORARIO_DIFERENTE', data_inicio=hoje + timedelta(13),
                data_fim=hoje + timedelta(13), hora_inicio=time(10), hora_fim=time(11), ativo=False)
        # bloqueio pontual cruzando a meia-noite (P1) e um inativo
        excecao(profissional=cls.p1, tipo='BLOQUEIO', data_hora_inicio=_local(hoje + timedelta(3), 22),
                data_hora_fim=_local(hoje + timedelta(4), 9, 30))
        excecao(profissional=cls.p2, tipo='BLOQUEIO', data_hora_inicio=_local(hoje + timedelta(2), 9),
                data_hora_fim=_local(hoje + timedelta(2), 17), ativo=False)
        # recorrentes (subconjunto da RRULE), com e sem data_fim_recorrencia
        dtstart_nao_casa = hoje + timedelta(2)                       # quinta-feira
        cls.recorrentes = [
            excecao(profissional=cls.p1, tipo='BLOQUEIO', regra_recorrencia='FREQ=DAILY;INTERVAL=3;BYDAY=MO,TU',
                    data_hora_inicio=_local(hoje - timedelta(14), 12), data_hora_fim=_local(hoje - timedelta(14), 13)),
            excecao(profissional=cls.p2, tipo='BLOQUEIO', regra_recorrencia='FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR',
                    data_hora_inicio=_local(hoje - timedelta(4), 15), data_hora_fim=_local(hoje - timedelta(4), 16, 30),
                    data_fim_recorrencia=hoje + timedelta(45)),
            excecao(tipo='BLOQUEIO', regra_recorrencia='FREQ=MONTHLY;BYDAY=1SA',
                    data_hora_inicio=_local(date(2026, 10, 3), 9), data_hora_fim=_local(date(2026, 10, 3), 11)),
            excecao(profissional=cls.p1, tipo='BLOQUEIO', regra_recorrencia='FREQ=MONTHLY;BYDAY=-1FR',
                    data_hora_inicio=_local(date(2026, 9, 25), 16), data_hora_fim=_local(date(2026, 9, 25), 17),
                    data_fim_recorrencia=date(2026, 12, 15)),
            excecao(profissional=cls.p2, tipo='BLOQUEIO', regra_recorrencia='FREQ=MONTHLY',     # DTSTART dia 31
                    data_hora_inicio=_local(date(2026, 8, 31), 10), data_hora_fim=_local(date(2026, 8, 31), 10, 30)),
            excecao(profissional=cls.p1, tipo='BLOQUEIO', regra_recorrencia='FREQ=WEEKLY;BYDAY=WE',
                    data_hora_inicio=_local(dtstart_nao_casa, 11), data_hora_fim=_local(dtstart_nao_casa, 11, 30)),
            excecao(tipo='BLOQUEIO', regra_recorrencia='FREQ=DAILY',                         # almoco global
                    data_hora_inicio=_local(hoje + timedelta(8), 12, 30), data_hora_fim=_local(hoje + timedelta(8), 13),
                    data_fim_recorrencia=hoje + timedelta(20)),
            excecao(profissional=cls.p2, tipo='BLOQUEIO', regra_recorrencia='FREQ=MONTHLY;BYMONTHDAY=15',
                    data_hora_inicio=_local(date(2026, 10, 15), 23), data_hora_fim=_local(date(2026, 10, 16), 9, 30)),
        ]
        # DTSTART que NAO casa com a regra (BYDAY=WE numa quinta): nao e ocorrencia
        assert cls.recorrentes[5].data_hora_inicio.astimezone(TZ).isoweekday() != 3

        def atendimento(prof, proc, inicio, status, fim=None):
            return Atendimento.objects.create(
                cliente=cli, profissional=prof, procedimento=proc, status=status, data_hora_inicio=inicio,
                data_hora_fim=fim or inicio + timedelta(minutes=proc.duracao_minutos), valor_cobrado=Decimal('0'),
            )

        atendimento(cls.p1, cls.proc_b, _local(hoje + timedelta(1), 9), 'PENDENTE')
        atendimento(cls.p1, cls.proc_a, _local(hoje + timedelta(1), 14), 'AGENDADO')
        atendimento(cls.p1, cls.proc_a, _local(hoje + timedelta(2), 10), 'CANCELADO')      # nao ocupa
        cls.ignorado = atendimento(cls.p2, cls.proc_c, _local(hoje + timedelta(1), 9, 30), 'CONFIRMADO')
        atendimento(cls.p2, cls.proc_b, _local(hoje + timedelta(3), 16), 'AGENDADO')
        atendimento(cls.p2, cls.proc_b, _local(hoje + timedelta(20), 23, 30), 'AGENDADO')   # cruza a meia-noite
        atendimento(cls.p2, cls.proc_a, _local(hoje, 11), 'AGENDADO')                       # hoje, depois de agora

    def _sql(self, prof, dia, proc=None, ignorar=None):
        return [t.strftime('%H:%M') for (t,) in _q(
            'SELECT * FROM fn_horarios_livres(%s, %s, %s, %s, %s)',
            [prof.pk, dia, proc.pk if proc else None, ignorar, AGORA],
        )]

    def test_fn_horarios_livres_igual_ao_slot_service(self):
        divergencias, comparados, com_slot = [], 0, 0
        with mock.patch('django.utils.timezone.now', return_value=AGORA):
            for prof in (self.p1, self.p2):
                for proc in (None, self.proc_a, self.proc_b, self.proc_c):
                    for delta in self.DIAS:
                        dia = AGORA.date() + timedelta(delta)
                        python = SlotService.slots_livres(prof, dia, proc)
                        sql = self._sql(prof, dia, proc)
                        comparados += 1
                        com_slot += bool(python)
                        if python != sql:
                            divergencias.append((prof.nome, proc and proc.nome, dia, python, sql))
            for delta in (1, 2):
                dia = AGORA.date() + timedelta(delta)
                python = SlotService.slots_livres(self.p2, dia, self.proc_b, ignorar_atendimento_id=self.ignorado.pk)
                sql = self._sql(self.p2, dia, self.proc_b, self.ignorado.pk)
                comparados += 1
                if python != sql:
                    divergencias.append(('ignorar', dia, python, sql))
        self.assertEqual(divergencias, [])
        self.assertEqual(comparados, 2 * 4 * len(self.DIAS) + 2)
        self.assertGreater(com_slot, 200)  # a matriz exercita dias com slots (nao so listas vazias)

    def test_fn_ocorrencias_recorrencia_igual_ao_dateutil(self):
        divergencias, ocorrencias = [], 0
        janelas = [(_local(AGORA.date() + timedelta(d), 0), _local(AGORA.date() + timedelta(d + 1), 0))
                   for d in range(-20, 70)]
        janelas += [(_local(date(2026, m, 1), 0), _local(date(2026 + (m == 12), m % 12 + 1, 1), 0))
                    for m in (9, 10, 11, 12)]
        for e in self.recorrentes:
            for ini, fim in janelas:
                python = [(a.astimezone(TZ), b.astimezone(TZ)) for a, b in e.ocorrencias(ini, fim)]
                sql = [(r.lower.astimezone(TZ), r.upper.astimezone(TZ)) for (r,) in _q(
                    'SELECT * FROM fn_ocorrencias_recorrencia(%s, %s, %s, %s, %s, %s)',
                    [e.regra_recorrencia, e.data_hora_inicio, e.data_hora_fim - e.data_hora_inicio,
                     e.data_fim_recorrencia, ini, fim],
                )]
                ocorrencias += len(python)
                if sorted(python) != sorted(sql):
                    divergencias.append((e.regra_recorrencia, ini.date(), python, sql))
        self.assertEqual(divergencias, [])
        self.assertGreater(ocorrencias, 50)
        # DTSTART fora da regra nao vira ocorrencia (dateutil): a quinta do DTSTART nao aparece
        nao_casa = self.recorrentes[5]
        ini = nao_casa.data_hora_inicio
        self.assertEqual(nao_casa.ocorrencias(ini - timedelta(hours=1), ini + timedelta(hours=1)), [])


# ═════════════════════════════════════════════════════════════════════════════
# Pacote, NPS, comissao, consentimento, financeiro, candidatos LGPD
# ═════════════════════════════════════════════════════════════════════════════
@skipUnless(PG, 'funcoes/views de leitura so existem no Postgres (0057)')
class ParidadeLeituraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prof = Profissional.objects.create(nome='Dra. Paridade')
        cls.proc = Procedimento.objects.create(nome='Peeling', duracao_minutos=30)
        cls.proc2 = Procedimento.objects.create(nome='Drenagem', duracao_minutos=30)
        cls.cli = criar_cliente(nome='Ana Paridade')

    def _realizado(self, cliente=None, proc=None, dias=10, valor='100.00', quando=None, **campos):
        at = criar_atendimento(cliente or self.cli, self.prof, proc or self.proc,
                               data_hora=quando or timezone.now() - timedelta(days=dias), status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal(valor), **campos)
        at.refresh_from_db()
        return at

    # ─── pacote ──────────────────────────────────────────────────────────
    def _combo(self, nome='Combo'):
        pacote = Pacote.objects.create(nome=nome, preco_total=Decimal('500.00'), validade_meses=6)
        ItemPacote.objects.create(pacote=pacote, procedimento=self.proc, quantidade_sessoes=3)
        ItemPacote.objects.create(pacote=pacote, procedimento=self.proc2, quantidade_sessoes=2)
        return pacote

    def test_fn_saldo_pacote_e_vw_pacote_saldo(self):
        parcial = criar_compra_pacote(self.cli, self._combo())
        cheia = criar_compra_pacote(criar_cliente(nome='Bia'), self._combo('Combo 2'))
        cancelada = criar_compra_pacote(criar_cliente(nome='Cleo'), self._combo('Combo 3'))
        ConsumoSessao.objects.create(compra_pacote=parcial, atendimento=self._realizado(dias=3))
        for dias, proc in ((4, self.proc), (5, self.proc), (6, self.proc), (7, self.proc2), (8, self.proc2)):
            ConsumoSessao.objects.create(compra_pacote=cheia, atendimento=self._realizado(cheia.cliente, proc, dias))
        ConsumoSessao.objects.create(compra_pacote=cancelada, atendimento=self._realizado(cancelada.cliente, dias=9))
        CompraPacote.objects.filter(pk=cancelada.pk).update(status='CANCELADO', valor_reembolsado=Decimal('100.00'))
        # "item reconstruido" (0052: procedimento consumido que saiu do catalogo; valor 0)
        antigo = Procedimento.objects.create(nome='Procedimento Antigo', duracao_minutos=30)
        CompraPacoteItem.objects.create(compra_pacote=parcial, procedimento=antigo,
                                        procedimento_nome='Procedimento Antigo', quantidade_sessoes=1,
                                        valor_unitario=Decimal('0'))
        ConsumoSessao.objects.create(compra_pacote=parcial, atendimento=self._realizado(proc=antigo, dias=2))
        # catalogo editado depois da venda nao muda a compra (itens congelados)
        ItemPacote.objects.filter(pacote__nome='Combo').update(quantidade_sessoes=10)
        for compra in (parcial, cheia, cancelada):
            compra = CompraPacote.objects.get(pk=compra.pk)
            python = [(lin['item'].pk, lin['procedimento'].pk, lin['procedimento_nome'], lin['total'],
                       lin['usadas'], lin['restantes']) for lin in compra.saldo_por_procedimento()]
            sql = _q('SELECT * FROM fn_saldo_pacote(%s)', [compra.pk])
            view = _q('SELECT compra_pacote_item_id, procedimento_id, procedimento_nome, qtd_total, qtd_usada, '
                      'qtd_restante FROM vw_pacote_saldo WHERE compra_pacote_id = %s ORDER BY 1', [compra.pk])
            self.assertEqual(python, sql, compra)
            self.assertEqual(python, view, compra)
        self.assertEqual([lin['restantes'] for lin in CompraPacote.objects.get(pk=parcial.pk).saldo_por_procedimento()],
                         [2, 2, 0])

    # ─── NPS (meio p/ cima: 1 promotor em 8 = 12,5 -> 13) ──────────────────
    def test_fn_nps_periodo_e_vw_nps_mensal(self):
        meses = {date(2026, 7, 1): [10, 8, 8, 8, 8, 8, 8, 8],    # +12,5 -> 13 (bancario daria 12)
                 date(2026, 8, 1): [0, 8, 8, 8, 8, 8, 8, 8],     # -12,5 -> -13
                 date(2026, 9, 1): [7, 8, 8, 8],                 # media 7,75 -> 7,8
                 date(2026, 10, 1): [9, 10, 6, 5, 7, 3]}
        for mes, notas in meses.items():
            for i, nota in enumerate(notas):
                aval = AvaliacaoNPS.objects.create(atendimento=self._realizado(dias=400 + i), nota=nota)
                AvaliacaoNPS.objects.filter(pk=aval.pk).update(criado_em=_local(mes, 12) + timedelta(days=i))
        campos = ('qtd_respostas', 'qtd_promotores', 'qtd_neutros', 'qtd_detratores', 'nps', 'nota_media')
        vw = {linha[0]: linha[1:] for linha in _q(
            'SELECT mes, qtd_respostas, qtd_promotores, qtd_neutros, qtd_detratores, nps, nota_media '
            'FROM vw_nps_mensal')}
        for mes in meses:
            fim = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
            ini_dt, fim_dt = _local(mes, 0), _local(fim, 0)
            python = resumo_nps(AvaliacaoNPS.objects.filter(criado_em__gte=ini_dt, criado_em__lt=fim_dt))
            esperado = tuple(python[c] for c in campos)
            (sql,) = _q('SELECT * FROM fn_nps_periodo(%s, %s)', [ini_dt, fim_dt])
            self.assertEqual(esperado, sql, mes)
            self.assertEqual(esperado, vw[mes], mes)
        self.assertEqual(vw[date(2026, 7, 1)][4], 13)
        self.assertEqual(vw[date(2026, 8, 1)][4], -13)
        self.assertEqual(vw[date(2026, 9, 1)][5], Decimal('7.8'))
        python = resumo_nps(AvaliacaoNPS.objects.all())
        (sql,) = _q('SELECT * FROM fn_nps_periodo()')
        self.assertEqual(tuple(python[c] for c in campos), sql)
        self.assertEqual(_q('SELECT * FROM fn_nps_periodo(%s, %s)', [AGORA, AGORA])[0][4], None)  # sem respostas

    # ─── comissao (livro-razao) ───────────────────────────────────────────
    def test_fn_saldo_comissao_vw_comissao_e_vw_comissao_saldo(self):
        prof2 = Profissional.objects.create(nome='Dra. Saldo Negativo')
        criar_comissao(self._realizado(dias=1), valor='30.00')                                # PENDENTE
        criar_comissao(self._realizado(dias=2), valor='40.00', status=MovimentoComissao.STATUS_PAGA)
        criar_comissao(self._realizado(dias=3), valor='50.00', status=MovimentoComissao.STATUS_ESTORNADA)
        at = criar_atendimento(self.cli, prof2, self.proc, data_hora=timezone.now() - timedelta(days=4),
                               status='REALIZADO')
        criar_comissao(at, valor='60.00', status='PAGA_ESTORNADA')                             # saldo -60
        python = {(c.pk, c.profissional_id, c.atendimento_id, c.valor, c.status, c.pago_em, c.estornado_em)
                  for c in MovimentoComissao.objects.creditos_com_status()}
        view = set(_q('SELECT movimento_comissao_id, profissional_id, atendimento_id, valor, status, pago_em, '
                      'estornado_em FROM vw_comissao'))
        self.assertEqual(python, view)
        saldos = ComissaoService.saldos_por_profissional()
        for prof in (self.prof, prof2):
            ((saldo_fn,),) = _q('SELECT fn_saldo_comissao(%s)', [prof.pk])
            (linha,) = _q('SELECT valor_saldo_a_pagar, valor_pendente FROM vw_comissao_saldo '
                          'WHERE profissional_id = %s', [prof.pk])
            pendente = sum((c.valor for c in MovimentoComissao.objects.creditos_com_status()
                            .filter(profissional=prof) if c.status == 'PENDENTE'), Decimal('0.00'))
            self.assertEqual(saldos.get(prof.pk, Decimal('0.00')), saldo_fn, prof)
            self.assertEqual((saldo_fn, pendente), linha, prof)
        self.assertEqual(_q('SELECT fn_saldo_comissao(%s)', [prof2.pk])[0][0], Decimal('-60.00'))
        # p_ate: so o que existia ate o instante
        ((antes,),) = _q('SELECT fn_saldo_comissao(%s, %s)', [self.prof.pk, timezone.now() - timedelta(days=30)])
        self.assertEqual(antes, Decimal('0.00'))

    # ─── consentimento ────────────────────────────────────────────────────
    def test_vw_consentimento_atual_e_fn_tem_consentimento(self):
        bia = criar_cliente(nome='Bia Consente')
        t0 = timezone.now() - timedelta(days=3)
        conceder(bia, 'consent_email_marketing', registrado_em=t0)                       # concede
        Consentimento.objects.create(cliente=bia, finalidade='MARKETING', canal='EMAIL', concedido=False,
                                     origem='DESCADASTRO', registrado_em=t0 + timedelta(days=1))  # revoga
        conceder(bia, 'consent_email_marketing', registrado_em=t0 + timedelta(days=2))   # reconcede
        conceder(bia, 'consent_whatsapp_nps', registrado_em=t0)
        Consentimento.objects.create(cliente=bia, finalidade='PESQUISA_SATISFACAO', canal='WHATSAPP',
                                     concedido=False, origem='PAINEL', registrado_em=t0)  # empate: id decide
        conceder(self.cli, 'consent_whatsapp_confirmacao')
        for cliente in (bia, self.cli, criar_cliente(nome='Sem Linha')):
            python = consentimentos.estado_atual(cliente)
            view = {(f, c): v for f, c, v in _q(
                'SELECT finalidade, canal, eh_concedido FROM vw_consentimento_atual WHERE cliente_id = %s',
                [cliente.pk])}
            for (fin, canal), concedido in python.items():
                ((fn,),) = _q('SELECT fn_tem_consentimento(%s, %s, %s)', [cliente.pk, fin, canal])
                self.assertEqual(concedido, fn, (cliente.nome, fin))
                self.assertEqual(concedido, consentimentos.vigente(cliente, fin, canal))
                self.assertEqual(concedido, view.get((fin, canal), False), (cliente.nome, fin))
        self.assertTrue(consentimentos.vigente(bia, 'MARKETING', 'EMAIL'))
        self.assertFalse(consentimentos.vigente(bia, 'PESQUISA_SATISFACAO', 'WHATSAPP'))

    # ─── financeiro: faturamento mensal, resumo do cliente, agenda ───────────
    def test_vw_faturamento_mensal_vw_cliente_resumo_e_vw_agenda_dia(self):
        bia = criar_cliente(nome='Bia Financeiro')
        meses = (date(2026, 8, 1), date(2026, 9, 1))
        pacote = Pacote.objects.create(nome='Glow', preco_total=Decimal('300.00'), validade_meses=6)
        ItemPacote.objects.create(pacote=pacote, procedimento=self.proc, quantidade_sessoes=3)
        compra = criar_compra_pacote(bia, pacote)
        cancelada = criar_compra_pacote(self.cli, pacote, valor_pago=Decimal('300.00'))
        devolvida = criar_compra_pacote(self.cli, pacote, valor_pago=Decimal('300.00'))
        CompraPacote.objects.filter(pk=compra.pk).update(criado_em=_local(meses[0], 10))
        CompraPacote.objects.filter(pk=cancelada.pk).update(criado_em=_local(meses[1], 10), status='CANCELADO',
                                                             valor_reembolsado=Decimal('120.00'))
        CompraPacote.objects.filter(pk=devolvida.pk).update(criado_em=_local(meses[1], 11), status='CANCELADO',
                                                             valor_reembolsado=Decimal('300.00'))
        hoje = timezone.localdate()
        ago, set_ = meses
        origem = self._realizado(bia, quando=_local(ago, 9), valor='150.00')
        self._realizado(bia, quando=_local(ago.replace(day=2), 10), valor='99.99')
        self._realizado(bia, quando=_local(ago.replace(day=3), 11), valor='0.00')            # sem valor: fora
        self._realizado(self.cli, quando=_local(set_, 9), valor='0.00', eh_retorno=True,   # retorno: fora
                        atendimento_origem=origem)
        sessao = self._realizado(bia, quando=_local(set_.replace(day=2), 10), valor='150.00')  # sessao de pacote
        ConsumoSessao.objects.create(compra_pacote=compra, atendimento=sessao)
        self._realizado(self.cli, quando=_local(set_.replace(day=30), 23, 30), valor='120.01')  # 23:30 local = outubro UTC
        faltou = criar_atendimento(bia, self.prof, self.proc, data_hora=timezone.now() - timedelta(days=5),
                                   status='FALTOU')
        futuro = criar_atendimento(bia, self.prof, self.proc, data_hora=timezone.now() + timedelta(days=3))
        Carteira.objects.create(cliente=bia, saldo=Decimal('25.50'))
        Prontuario.objects.create(cliente=bia, alergias='Latex')
        Prontuario.objects.create(cliente=self.cli)                                      # vazio: nao conta

        # vw_faturamento_mensal x services/financeiro (D20) por mes local
        view = {linha[0]: linha[1:] for linha in _q(
            'SELECT mes, valor_atendimentos, qtd_atendimentos, valor_pacotes, qtd_pacotes, valor_total, '
            'valor_ticket_medio FROM vw_faturamento_mensal')}
        meses_python = {timezone.localtime(a.data_hora_inicio).date().replace(day=1)
                        for a in financeiro.atendimentos_faturados()}
        meses_python |= {timezone.localtime(c.criado_em).date().replace(day=1) for c in financeiro.vendas_pacote()}
        self.assertEqual(set(view), meses_python)
        for mes in meses_python:
            fim = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
            ini_dt, fim_dt = _local(mes, 0), _local(fim, 0)
            av = financeiro.resumo_avulsos(Atendimento.objects.filter(data_hora_inicio__gte=ini_dt,
                                                                      data_hora_inicio__lt=fim_dt))
            pc = financeiro.resumo_pacotes(CompraPacote.objects.filter(criado_em__gte=ini_dt, criado_em__lt=fim_dt))
            esperado = (av['total'], av['qtd'], pc['total'], pc['qtd'], av['total'] + pc['total'],
                        av['ticket_medio'] if av['qtd'] else None)
            self.assertEqual(esperado, view[mes], mes)

        # vw_cliente_resumo x ORM/financeiro
        ((agora_db,),) = _q('SELECT now()')
        for cliente in (bia, self.cli):
            atds = Atendimento.objects.filter(cliente=cliente)
            realizados = atds.filter(status='REALIZADO').order_by('data_hora_inicio')
            proximo = atds.filter(status__in=('PENDENTE', 'AGENDADO', 'CONFIRMADO'),
                                  data_hora_inicio__gte=agora_db).order_by('data_hora_inicio').first()
            gasto = financeiro.valor_gasto_cliente(cliente)
            carteira = Carteira.objects.filter(cliente=cliente).first()
            esperado = (
                realizados.count(), atds.filter(status='FALTOU').count(), atds.filter(status='CANCELADO').count(),
                realizados.first() and realizados.first().data_hora_inicio,
                realizados.last() and realizados.last().data_hora_inicio,
                proximo and proximo.data_hora_inicio,
                gasto['avulsos'], gasto['pacotes'], gasto['total'],
                CompraPacote.objects.filter(cliente=cliente, status='ATIVO').count(),
                carteira.saldo if carteira else Decimal('0'),
                Prontuario.objects.filter(Prontuario.Q_COM_CONTEUDO, cliente=cliente).exists(),
                cliente.deletado_em is not None, cliente.nome.startswith('[ANONIMIZADO-'),
            )
            (linha,) = _q(
                'SELECT qtd_realizados, qtd_faltas, qtd_cancelados, primeiro_realizado_em, ultimo_realizado_em, '
                'proximo_atendimento_em, valor_avulsos, valor_pacotes, valor_total_gasto, qtd_pacotes_ativos, '
                'valor_saldo_carteira, tem_prontuario, eh_excluido, eh_anonimizado '
                'FROM vw_cliente_resumo WHERE cliente_id = %s', [cliente.pk])
            self.assertEqual(esperado, linha, cliente.nome)
        self.assertEqual(_q('SELECT proximo_atendimento_em FROM vw_cliente_resumo WHERE cliente_id = %s',
                            [bia.pk])[0][0], futuro.data_hora_inicio)

        # vw_agenda_dia x ORM (hora local, sessao de pacote)
        for at in Atendimento.objects.select_related('cliente', 'profissional', 'procedimento'):
            local_ini, local_fim = timezone.localtime(at.data_hora_inicio), timezone.localtime(at.data_hora_fim)
            esperado = (local_ini.date(), local_ini.time(), local_fim.time(), at.profissional.nome, at.cliente.nome,
                        at.procedimento.nome, at.status, ConsumoSessao.objects.filter(atendimento=at).exists())
            (linha,) = _q('SELECT data_local, hora_inicio_local, hora_fim_local, profissional_nome, cliente_nome, '
                          'procedimento_nome, status, eh_sessao_pacote FROM vw_agenda_dia WHERE atendimento_id = %s',
                          [at.pk])
            self.assertEqual(esperado, linha, at.pk)
        self.assertEqual(_q('SELECT count(*) FROM vw_agenda_dia')[0][0], Atendimento.objects.count())
        self.assertIsNotNone(faltou)
        self.assertLess(hoje, futuro.data_hora_inicio.date() + timedelta(days=1))

    def test_vw_atendimento_sem_desfecho(self):
        velho = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(days=3))
        criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(hours=2))
        criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(days=4),
                          status='PENDENTE')
        ((agora_db,),) = _q('SELECT now()')
        python = set(Atendimento.objects.filter(
            status__in=('AGENDADO', 'CONFIRMADO'), data_hora_fim__lt=agora_db - timedelta(hours=24),
        ).values_list('pk', flat=True))
        self.assertEqual(python, {pk for (pk,) in _q('SELECT atendimento_id FROM vw_atendimento_sem_desfecho')})
        self.assertEqual(python, {velho.pk})


# ═════════════════════════════════════════════════════════════════════════════
# LGPD: fn_clientes_para_anonimizar, sp_anonimizar_cliente, sp_aplicar_retencao_lgpd
# ═════════════════════════════════════════════════════════════════════════════
def _foto_lgpd():
    """Estado normalizado das tabelas que a anonimizacao/retencao tocam (ids novos e instantes fora)."""
    return {
        'cliente': _q(
            'SELECT id, nome, cpf, rg, email, telefone, cep, endereco, profissao, data_nascimento, url_foto, '
            'eh_ativo, deletado_em IS NOT NULL, qtd_faltas_consecutivas, eh_bloqueado_online '
            'FROM cliente ORDER BY id'),
        'consentimento': _ordenado(_q(
            'SELECT cliente_id, finalidade, canal, eh_concedido, origem, texto, host(ip_origem), user_agent, '
            'usuario_autor_id FROM consentimento')),
        'consentimento_vigente': _q(
            'SELECT cliente_id, finalidade, canal, eh_concedido, origem FROM vw_consentimento_atual ORDER BY 1, 2, 3'),
        'link_acesso': _q('SELECT id, finalidade, revogado_em IS NOT NULL FROM link_acesso ORDER BY id'),
        'codigo_otp': _q('SELECT id FROM codigo_otp ORDER BY id'),
        'lista_espera': _q('SELECT id FROM lista_espera ORDER BY id'),
        'notificacao': _q('SELECT id, mensagem FROM notificacao ORDER BY id'),
        'resposta_formulario': _q('SELECT id FROM resposta_formulario ORDER BY id'),
        'avaliacao_nps': _q('SELECT id, nota, comentario, eh_publicacao_autorizada, eh_publicacao_aprovada '
                            'FROM avaliacao_nps ORDER BY id'),
        'log_auditoria': _ordenado(_q(
            'SELECT usuario_autor_id, usuario_autor_nome, acao, tabela, registro_pk, detalhes::text, '
            'host(ip_origem) FROM log_auditoria')),
        'django_admin_log': _q('SELECT id, object_repr FROM django_admin_log ORDER BY id'),
        'prontuario': _q('SELECT id, cliente_id, alergias FROM prontuario ORDER BY id'),
        'atendimento': _q('SELECT id, cliente_id, status FROM atendimento ORDER BY id'),
    }


def _admin_log(usuario, obj):
    LogEntry.objects.create(
        user=usuario, content_type=ContentType.objects.get_for_model(obj, for_concrete_model=False),
        object_id=str(obj.pk), object_repr=str(obj), action_flag=CHANGE,
    )


@skipUnless(PG, 'procedures so existem no Postgres (0058)')
class ParidadeLgpdTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_user(email='dpo@clinica.com', password='senha-forte-1', nome='Dra. DPO',
                                                papel=Usuario.PAPEL_ADMIN)
        cls.prof = Profissional.objects.create(nome='Dra. LGPD')
        cls.proc = Procedimento.objects.create(nome='Limpeza', duracao_minutos=30)
        cls.form = FormularioAnamnese.objects.create(nome='Ficha de bem-estar')

    def _atd(self, cliente, status, dias):
        return criar_atendimento(cliente, self.prof, self.proc, data_hora=timezone.now() + timedelta(days=dias),
                                 status=status)

    def _ficha(self, cliente, atendimento=None, dias_atras=0, respondida=True, respostas=None):
        ficha = RespostaAnamnese.objects.create(
            formulario=self.form, cliente=cliente, atendimento=atendimento,
            respostas_json={'gestante': 'nao'} if respostas is None else respostas,
            respondida_em=timezone.now() if respondida else None,
        )
        if dias_atras:
            RespostaAnamnese.objects.filter(pk=ficha.pk).update(criado_em=timezone.now() - timedelta(days=dias_atras))
        return ficha

    def _titular(self, nome, telefone, email, cpf, dia_futuro=5):
        """Titular com rastro em todas as tabelas que o esquecimento toca."""
        cli = criar_cliente(nome=nome, telefone=telefone, email=email, cpf=cpf, rg='123456', cep='15000000',
                            endereco='Rua A, 1', profissao='Designer', data_nascimento=date(1990, 5, 1),
                            foto_url='https://exemplo.com/f.jpg')
        conceder(cli, 'consent_email_marketing', ip='10.0.0.1', user_agent='UA titular')
        conceder(cli, 'consent_whatsapp_confirmacao')
        conceder(cli, 'consent_whatsapp_nps')
        Consentimento.objects.create(cliente=cli, finalidade='PESQUISA_SATISFACAO', canal='WHATSAPP', concedido=False,
                                     origem='DESCADASTRO', ip_origem='10.0.0.2')
        realizado = self._atd(cli, 'REALIZADO', -30)
        cancelado = self._atd(cli, 'CANCELADO', -20)
        futuro = self._atd(cli, 'AGENDADO', dia_futuro)
        self._ficha(cli, realizado)
        ficha_cancelada = self._ficha(cli, cancelado)
        self._ficha(cli, None, respondida=False)
        criar_link('DESCADASTRO', cli)
        criar_link('GERENCIAR_AGENDAMENTO', futuro)
        criar_link('FORMULARIO', ficha_cancelada)
        notif, _token = criar_notificacao_com_link(realizado, tipo='NPS', mensagem=f'Oi {nome}, avalie')
        ja_revogado = LinkAcesso.objects.get(notificacao=notif)
        LinkAcesso.objects.filter(pk=ja_revogado.pk).update(revogado_em=timezone.now() - timedelta(days=1))
        AvaliacaoNPS.objects.create(atendimento=realizado, nota=9, comentario=f'Amei, {nome}',
                                    autoriza_publicacao=True, aprovado_publicacao=True)
        CodigoOtp.gerar_sms(telefone)
        CodigoOtp.gerar(email.upper())
        ListaEspera.objects.create(cliente=cli, procedimento=self.proc, data_desejada=timezone.localdate(),
                                   email_contato=email)
        pront = Prontuario.objects.create(cliente=cli, alergias='Dipirona')
        compra = criar_compra_pacote(cli, Pacote.objects.create(nome='P', preco_total=Decimal('1'), validade_meses=1))
        carteira = Carteira.objects.create(cliente=cli)
        LogAuditoria.objects.create(acao=f'Acessou prontuario de {nome}', tabela='prontuario', registro_id=cli.pk)
        LogAuditoria.objects.create(acao=f'Atualizou prontuario de {nome}', tabela='prontuario', registro_id=pront.pk)
        LogAuditoria.objects.create(acao=f'Vendeu pacote para {nome}', tabela='compra_pacote', registro_id=compra.pk)
        LogAuditoria.objects.create(acao=f'Agendou {nome}', tabela='atendimento', registro_id=futuro.pk)
        LogAuditoria.objects.create(acao=f'Editou cliente: {nome} Antiga', tabela='cliente', registro_id=cli.pk)
        LogAuditoria.objects.create(acao='migration 0034: telefone', tabela='cliente', registro_id=cli.pk,
                                    detalhes={'telefone': '17****0000'})
        for obj in (cli, futuro, compra, notif, carteira, AvaliacaoNPS.objects.get(atendimento=realizado),
                    ListaEspera.objects.get(cliente=cli)):
            _admin_log(self.admin, obj)
        return cli

    def test_sp_anonimizar_cliente_mesmo_efeito_do_python(self):
        titular = self._titular('Fernanda Titular', '17911112222', 'fernanda@exemplo.com', '52998224725')
        terceira = self._titular('Joana Terceira', '17933334444', 'joana@exemplo.com', '11144477735', dia_futuro=6)
        LogAuditoria.objects.create(acao='Nota sobre Fernanda Titular', tabela='cliente', registro_id=terceira.pk)
        request = RequestFactory().post('/django-admin-sv/', REMOTE_ADDR='203.0.113.9')
        request.user = self.admin

        def esquecer():
            LgpdService.esquecer_cliente(Cliente.all_objects.get(pk=titular.pk), usuario=self.admin, request=request)

        with FORCAR_PYTHON:
            _, python = _rodar_e_fotografar(esquecer, _foto_lgpd)
        with mock.patch.object(LgpdService, '_esquecer_cliente_python', side_effect=AssertionError('python')):
            _, sql = _rodar_e_fotografar(esquecer, _foto_lgpd)
        for tabela in python:
            self.assertEqual(python[tabela], sql[tabela], tabela)
        # a foto mudou de verdade (o teste nao compara dois "nada aconteceu")
        self.assertIn((titular.pk, f'[ANONIMIZADO-{titular.pk}]'), [(c[0], c[1]) for c in sql['cliente']])
        self.assertIn((self.admin.pk, 'Dra. DPO <dpo@clinica.com>',
                       'Cliente anonimizado (direito ao esquecimento / retencao LGPD)', 'cliente', titular.pk,
                       None, '203.0.113.9'), sql['log_auditoria'])
        self.assertIn(('Nota sobre Fernanda Titular'), [linha[2] for linha in sql['log_auditoria']])

    def test_sp_anonimizar_cliente_sem_autor_nem_request_e_inexistente(self):
        titular = self._titular('Rita Sem Autor', '17955556666', 'rita@exemplo.com', '39053344705')

        def esquecer():
            LgpdService.esquecer_cliente(Cliente.all_objects.get(pk=titular.pk))

        with FORCAR_PYTHON:
            _, python = _rodar_e_fotografar(esquecer, _foto_lgpd)
        _, sql = _rodar_e_fotografar(esquecer, _foto_lgpd)
        self.assertEqual(python, sql)
        from django.db import InternalError
        with self.assertRaises(InternalError), transaction.atomic():
            _q('CALL sp_anonimizar_cliente(%s, NULL, NULL)', [999999])

    def _cenario_retencao(self):
        """Um cliente por regra de candidatos_purga (e fichas/esperas da retencao)."""
        anos = timedelta(days=365)

        def cliente(nome, criado_ha=6 * anos, deletado_ha=None):
            c = criar_cliente(nome=nome)
            Cliente.all_objects.filter(pk=c.pk).update(
                criado_em=timezone.now() - criado_ha,
                deletado_em=timezone.now() - deletado_ha if deletado_ha else None,
                ativo=deletado_ha is None,
            )
            return c

        esperados = {
            cliente('Inativa Antiga'), cliente('Excluida Ha 40 Dias', criado_ha=anos, deletado_ha=timedelta(40)),
            cliente('Prontuario Vazio'),
        }
        Prontuario.objects.create(cliente=Cliente.all_objects.get(nome='Prontuario Vazio'))
        cliente('Excluida Ha 10 Dias', criado_ha=anos, deletado_ha=timedelta(10))
        cliente('[ANONIMIZADO-1]')
        self._atd(cliente('Atendimento Recente'), 'CANCELADO', -200)
        self._atd(cliente('Saude Retida'), 'REALIZADO', -365 * 10)
        Prontuario.objects.create(cliente=cliente('Prontuario Cheio'), alergias='Latex')
        pront = Prontuario.objects.create(cliente=cliente('Historico Clinico'))
        ProntuarioVersao.objects.create(prontuario=pront, dados={'alergias': 'antiga'})
        criar_compra_pacote(cliente('Comprou Pacote'), Pacote.objects.create(
            nome='Fiscal', preco_total=Decimal('1'), validade_meses=1))
        cliente('Recente', criado_ha=anos)
        # fichas de pedido nao realizado e lista de espera vencida
        dona = criar_cliente(nome='Dona das Fichas')
        self._ficha(dona, self._atd(dona, 'CANCELADO', -100), dias_atras=91)
        self._ficha(dona, self._atd(dona, 'PENDENTE', -100), dias_atras=91)
        self._ficha(dona, None, dias_atras=91, respondida=False)
        self._ficha(dona, self._atd(dona, 'FALTOU', -100), dias_atras=91, respondida=False, respostas={})
        self._ficha(dona, self._atd(dona, 'REALIZADO', -100), dias_atras=400)            # fica
        self._ficha(dona, self._atd(dona, 'CANCELADO', -5), dias_atras=10)               # recente: fica
        self._ficha(dona, self._atd(dona, 'FALTOU', -120), dias_atras=91)                # respondida: fica
        ListaEspera.objects.create(cliente=dona, procedimento=self.proc,
                                   data_desejada=timezone.localdate() - timedelta(days=1))
        ListaEspera.objects.create(cliente=dona, procedimento=self.proc, data_desejada=timezone.localdate())
        return {c.pk for c in esperados}

    def test_fn_clientes_para_anonimizar_igual_a_candidatos_purga(self):
        esperados = self._cenario_retencao()
        python = list(LgpdService.candidatos_purga().order_by('pk').values_list('pk', flat=True))
        sql = [pk for (pk,) in _q('SELECT * FROM fn_clientes_para_anonimizar(%s, %s, %s)', [
            LgpdService.RETENCAO_CLIENTE_INATIVO_DIAS, LgpdService.CARENCIA_SOFT_DELETE_DIAS,
            LgpdService.RETENCAO_ATENDIMENTO_SAUDE_DIAS])]
        self.assertEqual(python, sql)
        self.assertEqual(set(python), esperados)

    def test_sp_aplicar_retencao_lgpd_mesmo_efeito_do_python(self):
        self._cenario_retencao()
        with FORCAR_PYTHON:
            r_python, python = _rodar_e_fotografar(LgpdService.aplicar_retencao, _foto_lgpd)
        r_sql, sql = _rodar_e_fotografar(LgpdService.aplicar_retencao, _foto_lgpd)
        self.assertEqual(r_python, r_sql)
        self.assertEqual(r_sql, {'clientes': 3, 'fichas': 4, 'esperas': 1})
        for tabela in python:
            self.assertEqual(python[tabela], sql[tabela], tabela)


# ═════════════════════════════════════════════════════════════════════════════
# Housekeeping e expiracao de pacotes
# ═════════════════════════════════════════════════════════════════════════════
@skipUnless(PG, 'procedures so existem no Postgres (0058)')
class ParidadeManutencaoTests(TestCase):
    def setUp(self):
        self.prof = Profissional.objects.create(nome='Dra. Manutencao')
        self.proc = Procedimento.objects.create(nome='Limpeza', duracao_minutos=30)
        self.cli = criar_cliente(nome='Cliente Manutencao')

    def _foto(self):
        return {
            'codigo_otp': _q('SELECT id FROM codigo_otp ORDER BY id'),
            'notificacao': _q('SELECT id, mensagem FROM notificacao ORDER BY id'),
            'log_auditoria': _q('SELECT id FROM log_auditoria ORDER BY id'),
            'link_acesso': _q('SELECT id FROM link_acesso ORDER BY id'),
            'historico_alteracao': _q('SELECT id, tabela FROM historico_alteracao ORDER BY id'),
        }

    def test_sp_purgar_dados_expirados_mesmo_efeito_do_python(self):
        from aranha_estetica import tasks_manutencao as tm

        agora = timezone.now()
        _, velho = CodigoOtp.gerar('velho@x.com')
        CodigoOtp.gerar('novo@x.com')
        CodigoOtp.objects.filter(pk=velho.pk).update(criado_em=agora - timedelta(hours=25))
        at = criar_atendimento(self.cli, self.prof, self.proc)
        for dias in (400, 10):
            n = Notificacao.objects.create(atendimento=at, mensagem='Oi Cliente')
            Notificacao.objects.filter(pk=n.pk).update(criado_em=agora - timedelta(days=dias))
        Notificacao.objects.create(atendimento=at, mensagem='')
        for dias in (365 * 5 + 2, 30):
            log = LogAuditoria.objects.create(acao='x')
            LogAuditoria.objects.filter(pk=log.pk).update(criado_em=agora - timedelta(days=dias))
        # links: revogado/vencido ha mais e ha menos de 90 dias; sem validade (feed ICS) fica
        for revogado, expira in ((100, None), (10, None), (None, 100), (None, 10), (100, -10)):
            criar_link('GERENCIAR_AGENDAMENTO', at,
                       expira_em=agora - timedelta(days=expira) if expira is not None else agora + timedelta(days=30))
            if revogado:
                LinkAcesso.objects.filter(pk=LinkAcesso.objects.latest('id').pk).update(
                    revogado_em=agora - timedelta(days=revogado))
        criar_link('FEED_ICS', self.prof, expira_em=None)
        # trilha: antiga (preco: sai; prontuario: 20 anos, fica) e recente
        for tabela, dias in (('preco', 365 * 5 + 2), ('prontuario', 365 * 5 + 2), ('preco', 5)):
            _exec("INSERT INTO historico_alteracao (tabela, registro_pk, operacao, colunas_alteradas, dados_depois, "
               "transacao_ref, criado_em) VALUES (%s, 1, 'INSERT', '[]', '{}', 1, now() - make_interval(days => %s))",
               [tabela, dias])

        def python():
            return {nome: funcao() for nome, funcao in (
                ('otp', tm._purgar_otp), ('notificacoes', tm._limpar_texto_notificacoes),
                ('auditoria', tm._purgar_auditoria), ('links', tm._purgar_links),
                ('historico', tm._purgar_historico))}

        with FORCAR_PYTHON:
            r_python, f_python = _rodar_e_fotografar(python, self._foto)
        (r_sql, falhas), f_sql = _rodar_e_fotografar(tm._purgar_no_banco, self._foto)
        self.assertEqual(falhas, [])
        self.assertEqual(r_python, r_sql)
        self.assertEqual(r_sql, {'otp': 1, 'notificacoes': 1, 'auditoria': 1, 'links': 3, 'historico': 1})
        self.assertEqual(f_python, f_sql)
        # o job no PG usa a procedure e expoe as mesmas chaves do SQLite
        resumo = tm.job_housekeeping.apply().get()
        self.assertEqual({k: resumo[k] for k in r_sql}, r_sql)

    def test_sp_expirar_pacotes_mesmo_efeito_do_python(self):
        from aranha_estetica.tasks import job_expirar_pacotes

        pacote = Pacote.objects.create(nome='Validade', preco_total=Decimal('10'), validade_meses=1)
        hoje = timezone.localdate()
        compras = []
        for delta, status in ((-1, 'ATIVO'), (0, 'ATIVO'), (1, 'ATIVO'), (-5, 'CANCELADO'), (-5, 'FINALIZADO')):
            c = criar_compra_pacote(criar_cliente(), pacote)
            CompraPacote.objects.filter(pk=c.pk).update(data_expiracao=hoje + timedelta(days=delta), status=status)
            compras.append(c.pk)

        def foto():
            return _q('SELECT id, status FROM compra_pacote ORDER BY id')

        with FORCAR_PYTHON:
            r_python, f_python = _rodar_e_fotografar(lambda: job_expirar_pacotes.apply().get(), foto)
        r_sql, f_sql = _rodar_e_fotografar(lambda: job_expirar_pacotes.apply().get(), foto)
        self.assertEqual((r_python, f_python), (r_sql, f_sql))
        self.assertEqual(r_sql, '1 pacotes expirados')
        self.assertEqual(dict(f_sql)[compras[0]], 'EXPIRADO')
        self.assertEqual(dict(f_sql)[compras[1]], 'ATIVO')  # o ultimo dia ainda vale
