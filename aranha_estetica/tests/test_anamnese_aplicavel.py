"""Ficha aplicavel (regra unica, 3 escopos: global, categoria, procedimento), link da ficha p/ quem
agendou pelo painel e pesquisa pos-atendimento oferecida no fim do NPS."""
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import AvaliacaoNPS, FormularioAnamnese, RespostaAnamnese, Usuario
from aranha_estetica.services import anamnese
from aranha_estetica.services.alertas import ids_com_ficha_pendente

from .factories import (
    criar_atendimento, criar_cliente, criar_notificacao_com_link, criar_procedimento, criar_profissional,
)
from .test_confirmar_agendamento import slot_local, verificar_sessao

SCHEMA = [{'key': 'alergias', 'tipo': 'text', 'label': 'Alergias?', 'obrigatorio': True}]


def _form(escopo='GLOBAL', tipo='ANAMNESE', obrigatorio=True, **kw):
    return FormularioAnamnese.objects.create(
        nome=f'Ficha {escopo}', tipo=tipo, escopo=escopo, obrigatorio=obrigatorio, schema_json=SCHEMA, **kw,
    )


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)   # FACIAL
        self.outro = criar_procedimento(nome='Massagem', categoria='CORPORAL', profissional=self.prof)
        self.cli = criar_cliente(email='ficha@exemplo.test')


class RegraUnicaTests(_Base):
    def test_os_tres_escopos(self):
        casos = {
            _form('GLOBAL'): {self.proc, self.outro},
            _form('CATEGORIA', categoria='FACIAL'): {self.proc},
            _form('PROCEDIMENTO', procedimento=self.outro): {self.outro},
        }
        for form, valem in casos.items():
            for proc in (self.proc, self.outro):
                with self.subTest(escopo=form.escopo, proc=proc.nome):
                    esperado = proc in valem
                    self.assertEqual(form.aplica_a(proc), esperado)
                    self.assertEqual(
                        FormularioAnamnese.objects.aplicaveis_a(proc).filter(pk=form.pk).exists(), esperado)

    def test_escopo_modalidade_nao_existe_mais(self):
        """Todo atendimento e presencial (0070): o escopo MODALIDADE saiu das choices e do CHECK."""
        self.assertNotIn('MODALIDADE', dict(FormularioAnamnese.ESCOPO_CHOICES))
        with self.assertRaises(ValidationError):
            FormularioAnamnese(nome='Online', escopo='MODALIDADE', schema_json=SCHEMA).full_clean()

    def test_ficha_por_categoria_vira_pendencia_no_painel(self):
        _form('CATEGORIA', categoria='FACIAL')
        at = criar_atendimento(self.cli, self.prof, self.proc)
        outro = criar_atendimento(self.cli, self.prof, self.outro, data_hora=timezone.now() + timedelta(days=4))
        self.assertEqual(ids_com_ficha_pendente([at, outro]), {at.pk})

    @override_settings(RATELIMIT_ENABLE=False)
    def test_wizard_recebe_onde_cada_ficha_vale_e_exige_a_da_categoria(self):
        form = _form('CATEGORIA', categoria='FACIAL')
        client = Client()
        ctx = client.get(reverse('aranha:agendamento_publico')).context
        dados = {f['id']: f['procedimentos'] for f in ctx['formularios_anamnese_data']}
        self.assertEqual(dados[form.pk], [self.proc.pk])
        verificar_sessao(client, self.cli.telefone)
        client.post(reverse('aranha:confirmar_agendamento'), {
            'nome': self.cli.nome, 'telefone': self.cli.telefone, 'data_nascimento': '1990-03-20',
            'procedimento': self.proc.pk, 'profissional': self.prof.pk,
            'datetime': slot_local(dias=3, hora=11), 'aceite_politica': 'on',
        })
        self.assertFalse(self.cli.atendimento_set.exists())   # ficha obrigatoria sem resposta


class LinkDaFichaTests(_Base):
    def setUp(self):
        super().setUp()
        admin = Usuario.objects.create_user(
            email='admin-ficha@test.com', password='senha-forte-123', papel=Usuario.PAPEL_ADMIN,
        )
        self.client.force_login(admin)
        self.form = _form()
        self.at = criar_atendimento(self.cli, self.prof, self.proc)

    def test_gera_link_que_abre_a_ficha(self):
        resp = self.client.post(reverse('aranha:admin_link_ficha', args=[self.at.pk]))
        self.assertEqual(resp.status_code, 200)
        link = resp.context['fichas'][0][1]
        self.assertIn('/anamnese/', link)
        self.assertEqual(Client().get(link[link.index('/anamnese/'):]).status_code, 200)
        # 2o clique reaproveita o convite em aberto (sem resposta duplicada)
        self.client.post(reverse('aranha:admin_link_ficha', args=[self.at.pk]))
        self.assertEqual(RespostaAnamnese.objects.filter(atendimento=self.at).count(), 1)

    def test_botao_aparece_so_com_ficha_pendente(self):
        lista = self.client.get(reverse('aranha:painel_agendamentos') + '?periodo=todos')
        self.assertContains(lista, reverse('aranha:admin_link_ficha', args=[self.at.pk]))
        RespostaAnamnese.objects.create(formulario=self.form, cliente=self.cli, respostas_json={'alergias': 'não'},
                                        respondida_em=timezone.now())
        self.assertEqual(anamnese.fichas_pendentes(self.at), [])
        resp = self.client.post(reverse('aranha:admin_link_ficha', args=[self.at.pk]))
        self.assertEqual(resp.status_code, 302)


class PesquisaNoFimDoNpsTests(_Base):
    def _nps(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO',
                               data_hora=timezone.now() - timedelta(days=1))
        _notif, token = criar_notificacao_com_link(at, 'NPS', 'WHATSAPP', token='tok-nps-pesquisa', status='ENVIADO')
        return at, reverse('aranha:nps_web', args=[token])

    def test_oferece_a_pesquisa_ativa(self):
        _form('GLOBAL', tipo='PESQUISA', obrigatorio=False)
        at, url = self._nps()
        resp = self.client.post(url, {'nota': '9'}, follow=True)   # PRG: o convite sai no obrigado
        self.assertTrue(AvaliacaoNPS.objects.filter(atendimento=at).exists())
        self.assertIn('/pesquisa/', resp.context['link_pesquisa'])
        self.assertContains(resp, 'Tem mais um minutinho?')

    def test_sem_pesquisa_ativa_so_agradece(self):
        _at, url = self._nps()
        resp = self.client.post(url, {'nota': '9'}, follow=True)   # PRG: o convite sai no obrigado
        self.assertIsNone(resp.context['link_pesquisa'])
        self.assertNotContains(resp, 'Tem mais um minutinho?')
