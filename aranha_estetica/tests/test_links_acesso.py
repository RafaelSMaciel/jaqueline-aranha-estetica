"""Links publicos em hash (link_acesso, migration 0049 — spec padrao-sql §4.b).

Servico (emitir/resolver/marcar_usado/revogar/descartar), regras do model
(CHECK de alvo por finalidade, UNIQUE do hash) e regressao das rotas publicas:
token de outra finalidade, adulterado, revogado ou vencido nao abre nada.
"""
import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import FormularioAnamnese, LinkAcesso, Notificacao, RespostaAnamnese
from aranha_estetica.services import links

from .factories import (
    criar_atendimento, criar_cliente, criar_link, criar_notificacao_com_link, criar_procedimento,
    criar_profissional,
)


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.at = criar_atendimento(self.cli, self.prof, self.proc)


class ServicoLinksTests(_Base):
    def test_emitir_guarda_so_o_sha256_e_resolve_pelo_token(self):
        token = links.emitir(links.FEED_ICS, self.prof)
        self.assertGreaterEqual(len(token), 43)  # token_urlsafe(32): 256 bits
        link = LinkAcesso.objects.get()
        self.assertEqual(link.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(len(link.token_hash), 64)
        self.assertNotIn(token, str(LinkAcesso.objects.values().get()))
        self.assertEqual(links.resolver(links.FEED_ICS, token), link)
        self.assertEqual(links.resolver([links.NPS, links.FEED_ICS], token), link)
        self.assertEqual(link.alvo, self.prof)

    def test_dois_links_nunca_tem_o_mesmo_token(self):
        self.assertNotEqual(links.emitir(links.FEED_ICS, self.prof), links.emitir(links.FEED_ICS, self.prof))

    def test_finalidade_errada_nao_resolve(self):
        token = criar_link('DESCADASTRO', self.cli)
        for finalidade in ('GERENCIAR_AGENDAMENTO', 'CONFIRMAR_PRESENCA', 'NPS', 'TERMO', 'FORMULARIO', 'FEED_ICS'):
            with self.subTest(finalidade=finalidade):
                self.assertIsNone(links.resolver(finalidade, token))
        self.assertIsNotNone(links.resolver('DESCADASTRO', token))

    def test_token_adulterado_vazio_ou_invalido_nao_resolve(self):
        token = criar_link('DESCADASTRO', self.cli)
        trocado = ('A' if token[0] != 'A' else 'B') + token[1:]
        for ruim in (trocado, token[:-1], token + 'x', token.upper(), '', '   ', None, 5,
                     hashlib.sha256(token.encode()).hexdigest(), 'x' * 129):
            with self.subTest(ruim=ruim):
                self.assertIsNone(links.resolver('DESCADASTRO', ruim))
        self.assertIsNotNone(links.resolver('DESCADASTRO', f'  {token}  '))  # espacos da URL colada

    def test_expirado_so_com_aceitar_expirado(self):
        token = criar_link('NPS', Notificacao.objects.create(atendimento=self.at, tipo='NPS'),
                           expira_em=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(links.resolver('NPS', token))
        link = links.resolver('NPS', token, aceitar_expirado=True)
        self.assertTrue(link.expirado)
        sem_validade = criar_link('FEED_ICS', self.prof, expira_em=None)
        self.assertFalse(links.resolver('FEED_ICS', sem_validade).expirado)

    def test_revogar_so_o_alvo_e_a_finalidade(self):
        t1 = criar_link('FEED_ICS', self.prof)
        t2 = criar_link('FEED_ICS', self.prof)
        outro = criar_link('FEED_ICS', criar_profissional(nome='Dra. Outra'))
        descad = criar_link('DESCADASTRO', self.cli)
        self.assertEqual(links.revogar(links.FEED_ICS, self.prof), 2)
        self.assertEqual(links.revogar(links.FEED_ICS, self.prof), 0)  # ja revogados
        for token in (t1, t2):
            self.assertIsNone(links.resolver('FEED_ICS', token))
            self.assertIsNone(links.resolver('FEED_ICS', token, aceitar_expirado=True))  # revogado = 404 sempre
        self.assertIsNotNone(links.resolver('FEED_ICS', outro))
        self.assertIsNotNone(links.resolver('DESCADASTRO', descad))

    def test_gerar_link_feed_ics_revoga_o_anterior(self):
        antigo = criar_link('FEED_ICS', self.prof)
        novo = links.gerar_link_feed_ics(self.prof)
        self.assertIsNone(links.resolver('FEED_ICS', antigo))
        self.assertEqual(links.resolver('FEED_ICS', novo).profissional, self.prof)
        self.assertEqual(links.vigente('FEED_ICS', self.prof).token_hash, links.sha256_hex(novo))

    def test_marcar_usado_grava_uma_vez(self):
        link = links.resolver('FEED_ICS', criar_link('FEED_ICS', self.prof))
        links.marcar_usado(link)
        primeiro = LinkAcesso.objects.get(pk=link.pk).usado_em
        self.assertIsNotNone(primeiro)
        links.marcar_usado(LinkAcesso.objects.get(pk=link.pk))
        self.assertEqual(LinkAcesso.objects.get(pk=link.pk).usado_em, primeiro)

    def test_descartar_apaga_so_o_link_do_token(self):
        t1 = links.emitir(links.DESCADASTRO, self.cli)
        t2 = links.emitir(links.DESCADASTRO, self.cli)
        links.descartar(t1)
        links.descartar('')
        self.assertIsNone(links.resolver('DESCADASTRO', t1))
        self.assertIsNotNone(links.resolver('DESCADASTRO', t2))

    def test_vigente_ignora_revogado_e_vencido(self):
        self.assertIsNone(links.vigente('FEED_ICS', self.prof))
        criar_link('FEED_ICS', self.prof, expira_em=timezone.now() - timedelta(days=1))
        self.assertIsNone(links.vigente('FEED_ICS', self.prof))
        criar_link('FEED_ICS', self.prof)
        self.assertIsNotNone(links.vigente('FEED_ICS', self.prof))

    def test_emitir_link_formulario_rota_por_tipo_e_60_dias(self):
        for tipo, rota in (('ANAMNESE', '/anamnese/'), ('PESQUISA', '/pesquisa/')):
            form = FormularioAnamnese.objects.create(nome=tipo, tipo=tipo, schema_json=[])
            ficha = RespostaAnamnese.objects.create(formulario=form, cliente=self.cli)
            caminho = links.emitir_link_formulario(ficha)
            self.assertTrue(caminho.startswith(rota))
            link = LinkAcesso.objects.get(resposta_formulario=ficha)
            self.assertAlmostEqual(link.expira_em - timezone.now(), timedelta(days=60), delta=timedelta(minutes=1))


class ModeloLinkAcessoTests(_Base):
    """CHECK/UNIQUE portaveis (valem no SQLite tambem)."""

    def _criar(self, **kw):
        kw.setdefault('token_hash', links.sha256_hex(links.gerar_token()))
        with transaction.atomic():
            return LinkAcesso.objects.create(**kw)

    def test_alvo_tem_que_bater_com_a_finalidade(self):
        notif = Notificacao.objects.create(atendimento=self.at)
        ruins = [
            {'finalidade': 'NPS', 'cliente': self.cli},
            {'finalidade': 'DESCADASTRO', 'profissional': self.prof},
            {'finalidade': 'FEED_ICS'},                                         # sem alvo
            {'finalidade': 'TERMO', 'notificacao': notif, 'cliente': self.cli},  # 2 alvos
            {'finalidade': 'GERENCIAR_AGENDAMENTO', 'notificacao': notif},
            {'finalidade': 'OUTRA', 'cliente': self.cli},
        ]
        for kw in ruins:
            with self.subTest(kw=kw), self.assertRaises(IntegrityError):
                self._criar(**kw)
        for finalidade, alvo in (('CONFIRMAR_PRESENCA', {'notificacao': notif}), ('NPS', {'notificacao': notif}),
                                 ('TERMO', {'notificacao': notif}), ('GERENCIAR_AGENDAMENTO', {'atendimento': self.at}),
                                 ('DESCADASTRO', {'cliente': self.cli}), ('FEED_ICS', {'profissional': self.prof})):
            self._criar(finalidade=finalidade, **alvo)

    def test_hash_unico(self):
        h = links.sha256_hex('mesmo-token')
        self._criar(finalidade='DESCADASTRO', cliente=self.cli, token_hash=h)
        with self.assertRaises(IntegrityError):
            self._criar(finalidade='FEED_ICS', profissional=self.prof, token_hash=h)

    def test_cascata_apaga_o_link_com_o_alvo(self):
        notif, token = criar_notificacao_com_link(self.at, 'NPS')
        notif.delete()
        self.assertIsNone(links.resolver('NPS', token, aceitar_expirado=True))
        self.assertFalse(LinkAcesso.objects.exists())

    def test_str_e_alvo(self):
        notif, _token = criar_notificacao_com_link(self.at, 'TERMO', 'EMAIL')
        link = LinkAcesso.objects.get()
        self.assertEqual(link.alvo, notif)
        self.assertIn('notificacao', str(link))


@override_settings(RATELIMIT_ENABLE=False)
class RotasPublicasSoAceitamASuaFinalidadeTests(_Base):
    """Matriz: cada rota publica com um token VALIDO de cada outra finalidade -> 404."""

    def setUp(self):
        super().setUp()
        realizado = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO',
                                      data_hora=timezone.now() - timedelta(days=1))
        form = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', schema_json=[])
        ficha = RespostaAnamnese.objects.create(formulario=form, cliente=self.cli, atendimento=self.at)
        self.tokens = {
            'GERENCIAR_AGENDAMENTO': criar_link('GERENCIAR_AGENDAMENTO', self.at),
            'CONFIRMAR_PRESENCA': criar_notificacao_com_link(self.at)[1],
            'NPS': criar_notificacao_com_link(realizado, 'NPS')[1],
            'TERMO': criar_notificacao_com_link(self.at, 'TERMO', 'EMAIL')[1],
            'FORMULARIO': criar_link('FORMULARIO', ficha),
            'DESCADASTRO': criar_link('DESCADASTRO', self.cli),
            'FEED_ICS': criar_link('FEED_ICS', self.prof),
        }

    def _status(self, finalidade_da_rota, token):
        rotas = {
            'GERENCIAR_AGENDAMENTO': lambda t: self.client.get(reverse('aranha:reagendar_horarios', args=[t]),
                                                               {'data': '2031-01-01'}),
            'CONFIRMAR_PRESENCA': lambda t: self.client.get(reverse('aranha:confirmar_presenca', args=[t])),
            'NPS': lambda t: self.client.get(reverse('aranha:nps_web', args=[t])),
            'TERMO': lambda t: self.client.get(reverse('aranha:termo_assinatura', args=[t])),
            'FORMULARIO': lambda t: self.client.get(reverse('aranha:anamnese_publica', args=[t])),
            'DESCADASTRO': lambda t: self.client.get(reverse('aranha:lgpd_unsubscribe', args=[t])),
            'FEED_ICS': lambda t: self.client.get(
                reverse('aranha:ics_feed_profissional', args=[self.prof.slug]), {'token': t}),
        }
        return rotas[finalidade_da_rota](token).status_code

    def test_matriz(self):
        for rota in self.tokens:
            self.assertNotEqual(self._status(rota, self.tokens[rota]), 404, rota)  # o proprio abre
            for finalidade, token in self.tokens.items():
                if finalidade == rota:
                    continue
                with self.subTest(rota=rota, token_de=finalidade):
                    self.assertEqual(self._status(rota, token), 404)

    def test_revogado_nao_abre_nenhuma_rota(self):
        LinkAcesso.objects.update(revogado_em=timezone.now())
        for rota, token in self.tokens.items():
            with self.subTest(rota=rota):
                self.assertEqual(self._status(rota, token), 404)


class DjangoAdminLinkAcessoTests(_Base):
    """Admin somente leitura (sem o hash) com a acao de revogar; profissional sem ics_token."""

    def setUp(self):
        super().setUp()
        from django.test import Client

        from .test_auth_equipe import _admin, _com_totp, _token

        self.admin = _admin(email='adm-links@test.com')
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})

    def test_lista_detalhe_e_revogar(self):
        token = criar_link('FEED_ICS', self.prof)
        link = LinkAcesso.objects.get()
        base = '/django-admin-sv/aranha_estetica/linkacesso/'
        self.assertEqual(self.c.get(base).status_code, 200)
        detalhe = self.c.get(f'{base}{link.pk}/change/')
        self.assertEqual(detalhe.status_code, 200)
        self.assertNotContains(detalhe, link.token_hash)
        self.assertEqual(self.c.get(f'{base}add/').status_code, 403)
        resp = self.c.post(base, {'action': 'acao_revogar', '_selected_action': [link.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(links.resolver('FEED_ICS', token))
        prof = self.c.get(f'/django-admin-sv/aranha_estetica/profissional/{self.prof.pk}/change/')
        self.assertEqual(prof.status_code, 200)
        self.assertNotContains(prof, 'ics_token')
