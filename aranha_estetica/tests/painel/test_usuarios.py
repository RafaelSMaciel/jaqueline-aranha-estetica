"""Usuarios da equipe pelo UsuarioForm/UsuarioEdicaoForm: as regras de dominio no clean() e o feed ICS
revogado no desligamento (decisao 30: a URL do calendario de quem saiu deixava de entregar nomes)."""
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.forms.cadastros import MSG_EMAIL_EM_USO, UsuarioEdicaoForm, UsuarioForm
from aranha_estetica.models import LinkAcesso, LogAuditoria, Usuario

from ..factories import criar_link, criar_profissional

SENHA = 'Senha-Forte-2026!'


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(email='dona@equipe.test', password=SENHA, nome='Dona',
                                                 papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Bia')
        self.prof.refresh_from_db()   # slug do feed
        self.usuario = Usuario.objects.create_user(email='bia@equipe.test', password=SENHA, nome='Bia',
                                                   papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof)

    def _editar(self, usuario, **campos):
        dados = {'nome': usuario.nome, 'email': usuario.email, 'papel': usuario.papel,
                 'profissional_id': usuario.profissional_id or '', 'ativo': '1'}
        dados.update(campos)
        return self.client.post(reverse('aranha:admin_editar_usuario', args=[usuario.pk]), dados)

    def _feed(self, token):
        return self.client.get(f'/agenda/{self.prof.slug}/feed.ics', {'token': token}).status_code


class UsuarioFormTests(_Base):
    def test_email_duplicado_mesma_mensagem_ao_criar_e_ao_editar(self):
        criar = UsuarioForm({'nome': 'Outra', 'email': ' BIA@Equipe.test ', 'papel': 'ADMIN', 'senha': SENHA})
        self.assertFalse(criar.is_valid())
        outro = Usuario.objects.create_user(email='ana@equipe.test', password=SENHA, nome='Ana',
                                            papel=Usuario.PAPEL_ADMIN)
        editar = UsuarioEdicaoForm({'nome': 'Ana', 'email': 'bia@equipe.test', 'papel': 'ADMIN', 'ativo': '1'},
                                   instance=outro, request_user=self.admin)
        self.assertFalse(editar.is_valid())
        self.assertEqual(criar.errors['email'], [MSG_EMAIL_EM_USO])
        self.assertEqual(editar.errors['email'], [MSG_EMAIL_EM_USO])
        # o proprio e-mail (em outra caixa) nao e duplicata
        proprio = UsuarioEdicaoForm({'nome': 'Bia', 'email': 'BIA@equipe.test', 'papel': 'PROFISSIONAL',
                                     'profissional_id': self.prof.pk, 'ativo': '1'},
                                    instance=self.usuario, request_user=self.admin)
        self.assertTrue(proprio.is_valid(), proprio.errors)
        self.assertEqual(proprio.cleaned_data['email'], 'bia@equipe.test')

    def test_tela_mostra_a_mesma_mensagem_e_o_erro_no_campo(self):
        resp = self.client.post(reverse('aranha:admin_criar_usuario'), {
            'nome': 'Outra', 'email': 'bia@equipe.test', 'papel': 'ADMIN', 'senha': SENHA})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(MSG_EMAIL_EM_USO, _mensagens(resp))
        self.assertContains(resp, 'id="email-erro"')
        self.assertContains(resp, 'value="Outra"')   # o digitado volta
        resp = self._editar(self.admin, email='bia@equipe.test')
        self.assertIn(MSG_EMAIL_EM_USO, _mensagens(resp))

    def test_papel_vazio_cria_profissional_e_exige_vinculo(self):
        form = UsuarioForm({'nome': 'Nova', 'email': 'nova@equipe.test', 'senha': SENHA})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['profissional_id'],
                         ['Usuário com papel Profissional precisa de um profissional vinculado.'])
        livre = criar_profissional('Dra. Livre')
        form = UsuarioForm({'nome': 'Nova', 'email': 'nova@equipe.test', 'senha': SENHA, 'profissional_id': livre.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data['papel'], form.cleaned_data['profissional']),
                         (Usuario.PAPEL_PROFISSIONAL, livre))

    def test_senha_fraca_recusada(self):
        form = UsuarioForm({'nome': 'Adm', 'email': 'adm2@equipe.test', 'papel': 'ADMIN', 'senha': '123'})
        self.assertFalse(form.is_valid())
        self.assertIn('senha', form.errors)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', DEBUG=False)
    def test_sem_provedor_de_email_exige_senha(self):
        form = UsuarioForm({'nome': 'Adm', 'email': 'adm2@equipe.test', 'papel': 'ADMIN'})
        self.assertEqual(form.errors['senha'], ['O envio de e-mail não está configurado: defina uma senha inicial.'])


class TrancarForaTests(_Base):
    def test_ultimo_admin_nao_pode_ser_rebaixado_nem_desativado(self):
        outro_admin = Usuario.objects.create_user(email='socia@equipe.test', password=SENHA, nome='Sócia',
                                                  papel=Usuario.PAPEL_ADMIN)
        Usuario.objects.filter(pk=self.admin.pk).update(ativo=True)
        # com outra admin ativa (a dona), rebaixar a socia vale
        form = UsuarioEdicaoForm({'nome': 'Sócia', 'email': 'socia@equipe.test', 'papel': 'PROFISSIONAL',
                                  'profissional_id': criar_profissional('Dra. Sócia').pk, 'ativo': '1'},
                                 instance=outro_admin, request_user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        # a dona como a unica admin ativa: a socia (admin) nao a rebaixa nem a desativa
        Usuario.objects.filter(pk=outro_admin.pk).update(ativo=False)
        self.admin.refresh_from_db()
        for campos in ({'papel': 'PROFISSIONAL', 'ativo': '1'}, {'papel': 'ADMIN'}):
            with self.subTest(campos=campos):
                form = UsuarioEdicaoForm({'nome': 'Dona', 'email': self.admin.email,
                                          'profissional_id': criar_profissional(f'P {len(campos)}').pk, **campos},
                                         instance=self.admin, request_user=outro_admin)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.non_field_errors(), ['É preciso manter ao menos um administrador ativo.'])

    def test_auto_rebaixamento_recusado(self):
        resp = self._editar(self.admin, papel='PROFISSIONAL', profissional_id=criar_profissional('Dra. X').pk)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Você não pode alterar o próprio papel nem desativar a própria conta.', _mensagens(resp))
        resp = self._editar(self.admin, ativo='')
        self.assertIn('Você não pode alterar o próprio papel nem desativar a própria conta.', _mensagens(resp))
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.papel, self.admin.ativo), (Usuario.PAPEL_ADMIN, True))

    def test_recusa_marca_o_checkbox_como_enviado(self):
        resp = self._editar(self.admin, ativo='')
        self.assertNotRegex(resp.content.decode(), r'name="ativo" value="1"\s+checked')


class FeedIcsNoDesligamentoTests(_Base):
    def setUp(self):
        super().setUp()
        self.token = criar_link(LinkAcesso.FEED_ICS, self.prof)
        self.assertEqual(self._feed(self.token), 200)

    def _assert_revogado(self, motivo):
        self.assertEqual(self._feed(self.token), 404)
        log = LogAuditoria.objects.filter(tabela='profissional', registro_id=self.prof.pk).latest('pk')
        self.assertIn('Revogou o link do feed ICS', log.acao)
        self.assertEqual(log.detalhes, {'motivo': motivo})

    def test_desativar_o_usuario_faz_o_feed_responder_404(self):
        resp = self.client.post(reverse('aranha:admin_desativar_usuario', args=[self.usuario.pk]))
        self.assertEqual(resp.status_code, 302)
        self._assert_revogado('login desativado')
        self.assertTrue(any('gere um link novo' in m for m in _mensagens(resp)))
        # reativar o login nao devolve o link antigo
        self.client.post(reverse('aranha:admin_desativar_usuario', args=[self.usuario.pk]))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.ativo)
        self.assertEqual(self._feed(self.token), 404)

    def test_desativar_pela_edicao_revoga(self):
        self.assertEqual(self._editar(self.usuario, ativo='').status_code, 302)
        self._assert_revogado('login desativado')

    def test_trocar_o_vinculo_revoga_o_feed_do_profissional_anterior(self):
        nova = criar_profissional('Dra. Nova')
        self.assertEqual(self._editar(self.usuario, profissional_id=nova.pk).status_code, 302)
        self._assert_revogado('vínculo do login trocado')

    def test_editar_sem_desligar_mantem_o_feed(self):
        self.assertEqual(self._editar(self.usuario, nome='Bia Souza').status_code, 302)
        self.assertEqual(self._feed(self.token), 200)
        self.assertFalse(LogAuditoria.objects.filter(acao__contains='feed ICS').exists())
