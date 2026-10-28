"""Sessao de pacote PREVISTA: antes do Realizado a recepcao e a cliente ja veem que o pacote cobre
(nunca o preco cheio) — mesma ordem do debito, descontando as sessoes ja marcadas."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Usuario
from aranha_estetica.services.agendamento import formatar_valor_atendimento
from aranha_estetica.services.pacotes import sessoes_previstas
from aranha_estetica.views.publico.verificacao import SESSAO_MEUS_AGENDAMENTOS

from .factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento, criar_profissional,
)


class SessaoPrevistaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.compra = criar_compra_pacote(self.cli, criar_pacote(nome='Glow', procedimento=self.proc, sessoes=2))

    def _at(self, dias):
        inicio = (timezone.now() + timedelta(days=dias)).replace(hour=10, minute=0, second=0, microsecond=0)
        return criar_atendimento(self.cli, self.prof, self.proc, data_hora=inicio)

    def test_desconta_as_sessoes_ja_marcadas_na_ordem(self):
        a1, a2, a3 = self._at(2), self._at(4), self._at(6)
        previstas = sessoes_previstas([a3, a1, a2])
        self.assertEqual(previstas[a1.pk], {'pacote': 'Glow', 'restam': 1})
        self.assertEqual(previstas[a2.pk], {'pacote': 'Glow', 'restam': 0})
        self.assertNotIn(a3.pk, previstas)   # o pacote acabou: essa e cobrada
        self.assertEqual(formatar_valor_atendimento(a1), 'Sessão do pacote Glow')
        self.assertNotEqual(formatar_valor_atendimento(a3), 'Sessão do pacote Glow')

    def test_pacote_vencido_na_data_nao_cobre(self):
        self.compra.data_expiracao = timezone.localdate() + timedelta(days=3)
        self.compra.save(update_fields=['data_expiracao'])
        depois = self._at(5)
        self.assertEqual(sessoes_previstas([depois]), {})

    def test_painel_e_portal_mostram_o_pacote(self):
        at = self._at(2)
        admin = Usuario.objects.create_user(email='admin-pac@test.com', password='senha-forte-123',
                                            papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(admin)
        dia = timezone.localdate(at.data_hora_inicio).isoformat()
        self.assertContains(self.client.get(reverse('aranha:painel_agendamentos'), {'data': dia}), 'Pacote · restam 1')
        cliente = Client()
        sessao = cliente.session
        sessao[SESSAO_MEUS_AGENDAMENTOS] = self.cli.telefone
        sessao.save()
        self.assertContains(cliente.get(reverse('aranha:meus_agendamentos')), 'Sessão do pacote Glow (restam 1)')
