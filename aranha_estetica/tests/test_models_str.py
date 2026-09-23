"""Testes que garantem que os __str__ dos models nao quebram e carregam contexto util."""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    AceiteTermo,
    AvaliacaoNPS,
    BloqueioAgenda,
    ListaEspera,
    Notificacao,
    Promocao,
    VersaoTermo,
)

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_compra_pacote,
    criar_procedimento,
    criar_profissional,
)


class ModelStrTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(nome='Joao Silva')
        self.prof = criar_profissional(nome='Dra. Clara')
        self.proc = criar_procedimento(nome='Peeling', profissional=self.prof)

    def test_atendimento_str_contem_cliente_e_procedimento(self):
        atd = criar_atendimento(self.cliente, self.prof, self.proc)
        texto = str(atd)
        self.assertIn('Joao Silva', texto)
        self.assertIn('Peeling', texto)

    def test_atendimento_str_em_horario_local(self):
        # banco devolve UTC: 13:00 UTC = 10:00 em Sao Paulo (labels do admin)
        inicio = datetime(2026, 9, 25, 13, 0, tzinfo=dt_timezone.utc)
        atd = criar_atendimento(self.cliente, self.prof, self.proc, data_hora=inicio)
        atd.refresh_from_db()
        self.assertIn('25/09/2026 10:00', str(atd))

    def test_bloqueio_str_em_horario_local(self):
        inicio = datetime(2026, 9, 25, 23, 30, tzinfo=dt_timezone.utc)
        bloqueio = BloqueioAgenda.objects.create(
            profissional=self.prof, data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(hours=1),
        )
        self.assertIn('25/09/2026 20:30', str(bloqueio))

    def test_notificacao_str_contem_cliente_e_tipo(self):
        atd = criar_atendimento(self.cliente, self.prof, self.proc)
        notif = Notificacao.objects.create(
            atendimento=atd, tipo='LEMBRETE', canal='WHATSAPP'
        )
        texto = str(notif)
        self.assertIn('Joao Silva', texto)
        self.assertIn('Lembrete', texto)

    def test_compra_pacote_str_contem_cliente_pacote_status(self):
        pacote = criar_pacote(nome='Plano Facial', procedimento=self.proc, sessoes=4)
        pc = criar_compra_pacote(self.cliente, pacote)
        texto = str(pc)
        self.assertIn('Joao Silva', texto)
        self.assertIn('Plano Facial', texto)
        self.assertIn('Ativo', texto)

    def test_bloqueio_agenda_str(self):
        inicio = timezone.now() + timedelta(days=1)
        bloqueio = BloqueioAgenda.objects.create(
            profissional=self.prof,
            data_hora_inicio=inicio,
            data_hora_fim=inicio + timedelta(hours=2),
            motivo='Ferias',
        )
        self.assertIn('Dra. Clara', str(bloqueio))

    def test_lista_espera_str(self):
        le = ListaEspera.objects.create(
            cliente=self.cliente,
            procedimento=self.proc,
            data_desejada=timezone.now().date() + timedelta(days=3),
        )
        texto = str(le)
        self.assertIn('Joao Silva', texto)
        self.assertIn('Peeling', texto)

    def test_avaliacao_nps_str_mostra_nota(self):
        atd = criar_atendimento(self.cliente, self.prof, self.proc)
        nps = AvaliacaoNPS.objects.create(atendimento=atd, nota=9)
        self.assertIn('9/10', str(nps))
        self.assertIn('Joao Silva', str(nps))

    def test_promocao_str(self):
        promo = Promocao.objects.create(
            procedimento=self.proc,
            nome='Black Friday',
            desconto_percentual=Decimal('20.00'),
            data_inicio=timezone.now().date(),
            data_fim=timezone.now().date() + timedelta(days=7),
        )
        self.assertEqual(str(promo), 'Black Friday')

    def test_versao_termo_e_aceite_str_legiveis(self):
        termo = VersaoTermo.objects.create(
            tipo='LGPD', titulo='Privacidade', conteudo='x', versao='2.1',
            vigente_desde=timezone.localdate(),
        )
        self.assertEqual(str(termo), 'LGPD / Privacidade v2.1')
        aceite = AceiteTermo.registrar(self.cliente, termo)
        self.assertIn('LGPD / Privacidade v2.1', str(aceite))
