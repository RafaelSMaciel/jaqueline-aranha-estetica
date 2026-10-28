"""NPS publico (/nps/<token>/): formulario 0-10, PRG para o obrigado, link usado, expirado e clique
duplo. (Funde os antigos test_integration_nps e test_nps_escala.)"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import AvaliacaoNPS
from aranha_estetica.services import links, nps

from ..factories import (
    criar_atendimento, criar_cliente, criar_notificacao_com_link, criar_procedimento, criar_profissional,
)

TOKEN = 'tok-nps-publico-123'


@override_settings(RATELIMIT_ENABLE=False)
class NpsPublicoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(nome='Maria Silva')
        self.atd = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        self.notif, _token = criar_notificacao_com_link(self.atd, 'NPS', 'WHATSAPP', token=TOKEN, status='ENVIADO')
        self.url = reverse('aranha:nps_web', args=[TOKEN])

    def _link(self):
        return self.notif.links_acesso.get()

    def test_formulario_tem_os_11_botoes_de_0_a_10(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'publico/nps_web.html')
        for nota in range(11):
            self.assertContains(resp, f'data-nota="{nota}"')
        self.assertNotContains(resp, 'data-nota="11"')

    def test_nota_valida_grava_marca_o_link_e_redireciona_para_o_obrigado(self):
        resp = self.client.post(self.url, {'nota': '9', 'comentario': 'Excelente!'})
        self.assertRedirects(resp, reverse('aranha:nps_obrigado', args=[TOKEN]))
        avaliacao = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual((avaliacao.nota, avaliacao.comentario, avaliacao.autoriza_publicacao), (9, 'Excelente!', False))
        self.assertIsNotNone(self._link().usado_em)
        resp = self.client.get(reverse('aranha:nps_obrigado', args=[TOKEN]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'publico/nps_obrigado.html')
        self.assertContains(resp, 'Obrigado, Maria!')

    def test_nota_zero_e_aceita(self):
        self.client.post(self.url, {'nota': '0'})
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 0)

    def test_nota_fora_da_escala_nao_cria(self):
        for nota in ('11', '', 'dez', '07'):
            with self.subTest(nota=nota):
                resp = self.client.post(self.url, {'nota': nota})
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, 'Escolha uma nota de 0 a 10.')
        self.assertFalse(AvaliacaoNPS.objects.exists())
        self.assertIsNone(self._link().usado_em)

    def test_comentario_longo_demais_nao_cria(self):
        resp = self.client.post(self.url, {'nota': '8', 'comentario': 'x' * (nps.TAMANHO_MAXIMO_COMENTARIO + 1)})
        self.assertContains(resp, 'Comentário com no máximo 2000 caracteres.')
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_ja_respondida_nao_sobrescreve(self):
        AvaliacaoNPS.objects.create(atendimento=self.atd, nota=7, comentario='Bom')
        resp = self.client.post(self.url, {'nota': '10', 'comentario': 'Perfeito'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'publico/nps_obrigado.html')
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 7)
        self.assertTrue(self.client.get(self.url).context['ja_respondeu'])

    def test_clique_duplo_nao_da_500(self):
        """Duas chamadas a registrar_avaliacao (o 2o POST chega antes do 1o terminar): 1 avaliacao."""
        link = self._link()
        primeira, criada = nps.registrar_avaliacao(self.atd, 9, link=link)
        segunda, criada_de_novo = nps.registrar_avaliacao(self.atd, 3, link=link)
        self.assertEqual((criada, criada_de_novo), (True, False))
        self.assertEqual(primeira.pk, segunda.pk)
        self.assertEqual(AvaliacaoNPS.objects.get().nota, 9)
        for _ in range(2):
            self.assertEqual(self.client.post(self.url, {'nota': '5'}).status_code, 302)

    def test_nota_invalida_no_service(self):
        with self.assertRaises(ValueError):
            nps.registrar_avaliacao(self.atd, 11)
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_token_invalido_404(self):
        self.assertEqual(self.client.get(reverse('aranha:nps_web', args=['token-invalido-xyz'])).status_code, 404)
        self.assertEqual(self.client.get(reverse('aranha:nps_obrigado', args=['token-invalido-xyz'])).status_code, 404)

    def test_expirado_410_e_nao_grava(self):
        _notif, token = criar_notificacao_com_link(
            self.atd, 'NPS', 'WHATSAPP', status='ENVIADO', expira_em=timezone.now() - timedelta(minutes=1),
        )
        url = reverse('aranha:nps_web', args=[token])
        self.assertEqual(self.client.get(url).status_code, 410)
        resp = self.client.post(url, {'nota': '10'})
        self.assertEqual(resp.status_code, 410)
        self.assertTemplateUsed(resp, 'publico/nps_obrigado.html')
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_obrigado_sem_avaliacao_volta_ao_formulario(self):
        self.assertRedirects(self.client.get(reverse('aranha:nps_obrigado', args=[TOKEN])), self.url)

    def test_link_de_outra_finalidade_nao_abre(self):
        _notif, token = criar_notificacao_com_link(self.atd, 'LEMBRETE', 'WHATSAPP')
        self.assertIsNotNone(links.resolver(links.CONFIRMAR_PRESENCA, token))
        self.assertEqual(self.client.get(reverse('aranha:nps_web', args=[token])).status_code, 404)

