"""Regressao: Referrer-Policy em rotas com token e rotacao do token do feed ICS."""
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse

from aranha_estetica.models import LinkAcesso, LogAuditoria, Usuario
from aranha_estetica.tests.factories import criar_link, criar_profissional


class ReferrerPolicyTokenTests(TestCase):
    def test_rota_com_token_sai_sem_referer(self):
        resp = self.client.get('/confirmar/token-inexistente/')
        self.assertEqual(resp['Referrer-Policy'], 'no-referrer')

    def test_rota_publica_comum_mantem_politica_padrao(self):
        resp = self.client.get(reverse('aranha:inicio'))
        self.assertEqual(resp['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_token_e_area_privada_fora_do_indice_e_vitrine_dentro(self):
        for url in ('/confirmar/token-inexistente/', '/nps/x/', '/lgpd/unsubscribe/x/', '/meus-agendamentos/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url)['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertFalse(self.client.get(reverse('aranha:inicio')).has_header('X-Robots-Tag'))


def _token_da_url(url):
    return parse_qs(urlsplit(url).query)['token'][0]


class RotacionarIcsTests(TestCase):
    """0049: o painel gera o link (revoga o anterior) e RENDERIZA a URL uma unica vez."""

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-ics@test.com', password='senha-forte-123', nome='Admin ICS',
        )
        self.prof = criar_profissional()
        self.prof.refresh_from_db()
        self.url = reverse('aranha:profissional_rotacionar_ics', args=[self.prof.pk])
        self.antigo = criar_link('FEED_ICS', self.prof)

    def _feed(self, token):
        return self.client.get(f'/agenda/{self.prof.slug}/feed.ics', {'token': token}).status_code

    def test_post_gera_link_novo_mostra_uma_vez_e_audita(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)  # renderiza: token fora de URL de redirect/log
        self.assertTemplateUsed(resp, 'painel/ics_link.html')
        self.assertIn('no-store', resp['Cache-Control'])
        url = resp.context['link']
        self.assertTrue(url.startswith(f'http://testserver/agenda/{self.prof.slug}/feed.ics?token='))
        token = _token_da_url(url)
        self.assertContains(resp, 'por segurança este link não é exibido de novo')
        self.assertEqual(self._feed(token), 200)
        self.assertEqual(self._feed(self.antigo), 404)  # gerar = revogar o anterior
        # banco guarda so o hash; a lista nao reexibe o link
        self.assertFalse(LinkAcesso.objects.filter(token_hash=token).exists())
        self.assertEqual(LinkAcesso.objects.filter(profissional=self.prof, revogado_em__isnull=True).count(), 1)
        lista = self.client.get(reverse('aranha:painel_profissionais')).content.decode()
        self.assertNotIn(token, lista)
        self.assertIn('Link ativo desde', lista)
        self.assertTrue(LogAuditoria.objects.filter(
            tabela='profissional', registro_id=self.prof.pk, acao__icontains='ICS',
        ).exists())
        self.assertFalse(LogAuditoria.objects.filter(acao__contains=token).exists())

    def test_sem_link_a_lista_oferece_gerar(self):
        LinkAcesso.objects.filter(profissional=self.prof).delete()
        self.client.force_login(self.admin)
        lista = self.client.get(reverse('aranha:painel_profissionais')).content.decode()
        self.assertIn('Sem link', lista)
        self.assertIn('Gerar link', lista)

    def test_get_nao_altera(self):
        self.client.force_login(self.admin)
        self.client.get(self.url)
        self.assertEqual(self._feed(self.antigo), 200)

    def test_anonimo_nao_rotaciona(self):
        self.client.post(self.url)
        self.assertEqual(self._feed(self.antigo), 200)
        self.assertEqual(LinkAcesso.objects.filter(profissional=self.prof).count(), 1)


class LinkCalendarioPortalTests(TestCase):
    """O profissional gera o proprio link do feed no portal (mesma regra do painel)."""

    def setUp(self):
        self.prof = criar_profissional(nome='Dra. Portal')
        self.user = Usuario.objects.create_user(
            email='dra-portal@example.com', password='Senha-forte-123', nome='Dra. Portal',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self.url = reverse('aranha:profissional_link_calendario')

    def test_gera_o_proprio_link_e_revoga_o_anterior(self):
        antigo = criar_link('FEED_ICS', self.prof)
        outro_prof = criar_profissional(nome='Dra. Outra')
        do_outro = criar_link('FEED_ICS', outro_prof)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'profissional/ics_link.html')
        token = _token_da_url(resp.context['link'])
        feed = f'/agenda/{self.prof.slug}/feed.ics'
        self.assertEqual(self.client.get(feed, {'token': token}).status_code, 200)
        self.assertEqual(self.client.get(feed, {'token': antigo}).status_code, 404)
        # o link de outro profissional nao e tocado
        self.assertEqual(
            self.client.get(f'/agenda/{outro_prof.slug}/feed.ics', {'token': do_outro}).status_code, 200,
        )
        agenda = self.client.get(reverse('aranha:profissional_agenda')).content.decode()
        self.assertIn(self.url, agenda)

    def test_portal_tem_casca_propria_sem_o_site_publico(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('aranha:profissional_agenda'))
        self.assertTemplateUsed(resp, 'profissional/base.html')
        self.assertTemplateNotUsed(resp, 'estrutura/base_v2.html')
        html = resp.content.decode()
        self.assertIn('Portal da profissional', html)
        self.assertIn('aria-current="page"', html)
        self.assertNotIn('Falar no WhatsApp', html)          # sem o botao flutuante do site
        self.assertIn('noindex', html)
        self.assertEqual(html.count(reverse('aranha:usuario_logout')), 1)   # "Sair" so na casca
