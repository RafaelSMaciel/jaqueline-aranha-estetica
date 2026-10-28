"""Questionario unico (spec padrao-sql §4.d, migration 0051).

As perguntas extras do prontuario sao o FormularioAnamnese tipo PRONTUARIO
(no maximo 1 ativo, GLOBAL, nunca obrigatorio), editadas no CRUD de anamneses,
validadas pelo mesmo services/anamnese.validar_respostas, com as respostas em
Prontuario.respostas_extras pela key. Alertas de saude: alerta_saude
true/false por pergunta, ausente = heuristica (caracterizacao: o resultado com
os dados migrados e identico ao da regra antiga sobre a Configuracao).
"""
import importlib
import json
import re

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from aranha_estetica.models import (
    FormularioAnamnese, LogAuditoria, Prontuario, ProntuarioVersao, RespostaAnamnese, Usuario,
)
from aranha_estetica.services.lgpd import LgpdService
from aranha_estetica.utils import saude
from aranha_estetica.utils.saude import alertas_saude, perguntas_prontuario

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

M0051 = importlib.import_module('aranha_estetica.migrations.0051_formulario_prontuario')

PERGUNTAS = [
    {'key': 'fumante', 'tipo': 'bool', 'label': 'Fuma?', 'obrigatorio': False},
    {'key': 'obs_pele', 'tipo': 'longtext', 'label': 'Observações da pele', 'obrigatorio': False},
    {'key': 'fototipo', 'tipo': 'select', 'label': 'Fototipo', 'opcoes': ['I', 'II', 'III'], 'obrigatorio': False},
    {'key': 'areas', 'tipo': 'checkboxes', 'label': 'Áreas tratadas', 'opcoes': ['Rosto', 'Pescoço'],
     'obrigatorio': False},
    {'key': 'peso', 'tipo': 'number', 'label': 'Peso (kg)', 'obrigatorio': False},
]


def _questionario(perguntas=None, **kw):
    kw.setdefault('nome', 'Questionário do prontuário')
    return FormularioAnamnese.objects.create(tipo='PRONTUARIO', schema_json=perguntas or PERGUNTAS, **kw)


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_user(
            email='admin@questionario.com', password='senha123', papel=Usuario.PAPEL_ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.cli = criar_cliente(nome='Carla Lima')


class ModeloTests(TestCase):
    def test_no_maximo_um_prontuario_ativo(self):
        _questionario()
        _questionario(nome='Versão antiga', ativo=False)  # inativo pode
        with self.assertRaises(IntegrityError), transaction.atomic():
            _questionario(nome='Outro ativo')
        dup = FormularioAnamnese(nome='Outro', tipo='PRONTUARIO', schema_json=[])
        with self.assertRaisesMessage(ValidationError, 'Já existe um questionário do prontuário ativo'):
            dup.full_clean()

    def test_prontuario_sempre_global_e_nunca_obrigatorio(self):
        proc = criar_procedimento()
        for extra in ({'obrigatorio': True}, {'escopo': 'CATEGORIA', 'categoria': 'FACIAL'},
                      {'escopo': 'PROCEDIMENTO', 'procedimento': proc}):
            with self.subTest(extra=extra), self.assertRaises(IntegrityError), transaction.atomic():
                _questionario(**extra)
        with self.assertRaisesMessage(ValidationError, 'escopo Global e não obrigatório'):
            FormularioAnamnese(nome='X', tipo='PRONTUARIO', obrigatorio=True, schema_json=[]).full_clean()

    def test_escopo_e_tipo_no_dominio_e_coerentes(self):
        casos = (
            {'tipo': 'OUTRO'},
            {'escopo': 'QUALQUER'},
            {'escopo': 'PROCEDIMENTO'},
            {'escopo': 'CATEGORIA', 'categoria': ''},
            {'escopo': 'MODALIDADE'},                     # todo atendimento e presencial (0067)
        )
        for extra in casos:
            with self.subTest(extra=extra), self.assertRaises(IntegrityError), transaction.atomic():
                FormularioAnamnese.objects.create(nome='F', schema_json=[], **extra)

    def test_alerta_saude_precisa_ser_booleano(self):
        form = FormularioAnamnese(nome='F', tipo='ANAMNESE', schema_json=[
            {'key': 'x', 'tipo': 'text', 'label': 'X', 'alerta_saude': 'sim'},
        ])
        with self.assertRaisesMessage(ValidationError, 'alerta_saude deve ser true ou false'):
            form.full_clean()

    def test_prontuario_nao_recebe_resposta_avulsa(self):
        form = _questionario()
        resposta = RespostaAnamnese(formulario=form, cliente=criar_cliente(), respostas_json={})
        with self.assertRaises(ValidationError):
            resposta.full_clean()

    def test_perguntas_vem_do_formulario_ativo(self):
        self.assertEqual(perguntas_prontuario(), [])
        _questionario(nome='Inativo', ativo=False, perguntas=[{'key': 'velha', 'tipo': 'bool', 'label': 'V'}])
        self.assertEqual(perguntas_prontuario(), [])
        _questionario()
        self.assertEqual([p['key'] for p in perguntas_prontuario()], [p['key'] for p in PERGUNTAS])
        self.assertEqual(FormularioAnamnese.objects.prontuario_ativo().nome, 'Questionário do prontuário')


class FichaDoProntuarioTests(_Base):
    def setUp(self):
        super().setUp()
        self.form = _questionario()
        self.detalhe = reverse('aranha:prontuario_detalhe', args=[self.cli.pk])
        self.salvar = reverse('aranha:prontuario_salvar', args=[self.cli.pk])

    def _versao(self):
        return self.client.get(self.detalhe).context['versao_prontuario']

    def test_renderiza_cada_tipo_com_a_resposta_atual(self):
        Prontuario.objects.create(cliente=self.cli, respostas_extras={
            'fumante': True, 'fototipo': 'II', 'areas': ['Pescoço'], 'obs_pele': 'Oleosa',
        })
        html = self.client.get(self.detalhe).content.decode()
        self.assertRegex(html, r'name="pergunta_fumante" value="sim"[^>]*\s+checked')
        self.assertRegex(html, r'<option value="II" selected>')
        self.assertRegex(html, r'name="pergunta_areas" value="Pescoço"[^>]*\s+checked')
        self.assertNotRegex(html, r'name="pergunta_areas" value="Rosto"[^>]*\s+checked')
        self.assertIn('>Oleosa</textarea>', html)
        self.assertIn('type="number"', html)
        for p in PERGUNTAS:
            self.assertIn(p['label'], html)

    def test_salva_pelo_validador_unico_sem_apagar_o_que_nao_veio(self):
        Prontuario.objects.create(cliente=self.cli, respostas_extras={'chave_antiga': 'historica'})
        resp = self.client.post(self.salvar, {
            'versao': self._versao(), 'pergunta_fumante': 'sim', 'pergunta_fototipo': 'III',
            'pergunta_areas': ['Rosto', 'Pescoço'], 'pergunta_areas__enviado': '1', 'pergunta_peso': '61,5',
        })
        self.assertEqual(resp.status_code, 302)
        extras = Prontuario.objects.get(cliente=self.cli).respostas_extras
        self.assertEqual(extras, {
            'chave_antiga': 'historica', 'fumante': True, 'fototipo': 'III',
            'areas': ['Rosto', 'Pescoço'], 'peso': '61,5',
        })
        log = LogAuditoria.objects.get(acao='Atualizou prontuario')
        self.assertEqual(set(log.detalhes['campos_alterados']),
                         {'pergunta_fumante', 'pergunta_fototipo', 'pergunta_areas', 'pergunta_peso'})

    def test_resposta_invalida_nao_salva_nada(self):
        Prontuario.objects.create(cliente=self.cli, alergias='Lidocaina')
        resp = self.client.post(self.salvar, {
            'versao': self._versao(), 'alergias': 'Mudou', 'pergunta_fototipo': 'VII',
        }, follow=True)
        self.assertContains(resp, 'opção inválida')
        p = Prontuario.objects.get(cliente=self.cli)
        self.assertEqual((p.alergias, p.respostas_extras), ('Lidocaina', {}))
        self.assertFalse(ProntuarioVersao.objects.exists())

    def test_apagar_resposta_tira_do_json_e_versao_guarda_a_anterior(self):
        Prontuario.objects.create(cliente=self.cli, respostas_extras={'obs_pele': 'Oleosa', 'fumante': False})
        self.client.post(self.salvar, {
            'versao': self._versao(), 'pergunta_obs_pele': '  ', 'pergunta_fumante': 'nao',
            'pergunta_areas__enviado': '1',
        })
        self.assertEqual(Prontuario.objects.get(cliente=self.cli).respostas_extras, {'fumante': False})
        versao = ProntuarioVersao.objects.get()
        self.assertEqual(versao.dados['respostas_extras'], {'obs_pele': 'Oleosa', 'fumante': False})
        # sem mudanca real: nao grava nem versiona de novo
        self.client.post(self.salvar, {'versao': self._versao(), 'pergunta_fumante': 'nao'})
        self.assertEqual(ProntuarioVersao.objects.count(), 1)

    def test_historico_rotula_pelo_label_atual_e_chave_removida(self):
        Prontuario.objects.create(cliente=self.cli, respostas_extras={'fumante': False, 'saiu_do_form': 'x'})
        self.client.post(self.salvar, {'versao': self._versao(), 'pergunta_fumante': 'sim'})
        self.form.schema_json = [{**PERGUNTAS[0], 'label': 'É fumante?'}]
        self.form.save()
        historico = self.client.get(self.detalhe).context['historico_prontuario']
        self.assertEqual(historico[0]['mudancas'], [{'campo': 'É fumante?', 'antes': 'Não', 'depois': 'Sim'}])
        Prontuario.objects.filter(cliente=self.cli).update(respostas_extras={'fumante': True})
        html = self.client.get(self.detalhe).content.decode()
        self.assertIn('Saiu do form', html)

    def test_lista_do_prontuario_mostra_perguntas_e_link_de_edicao(self):
        resp = self.client.get(reverse('aranha:prontuario_consentimento'))
        self.assertEqual(resp.context['total_perguntas'], len(PERGUNTAS))
        self.assertContains(resp, reverse('aranha:admin_anamnese_editar', args=[self.form.pk]))
        self.assertContains(resp, 'Observações da pele')

    def test_dsar_traz_respostas_com_a_pergunta(self):
        Prontuario.objects.create(cliente=self.cli, respostas_extras={'fumante': True, 'sem_form': 'x'})
        dados = LgpdService.exportar_dados_cliente(self.cli)['prontuario']
        self.assertEqual(dados['questionario'], [
            {'pergunta': 'Fuma?', 'resposta': True}, {'pergunta': 'Sem form', 'resposta': 'x'},
        ])


class CrudAnamnesesTests(_Base):
    def _post(self, pk=None, **dados):
        url = (reverse('aranha:admin_anamnese_editar', args=[pk]) if pk
               else reverse('aranha:admin_anamnese_criar'))
        base = {'nome': 'Questionário do prontuário', 'tipo': 'PRONTUARIO', 'escopo': 'GLOBAL',
                'schema_json': json.dumps(PERGUNTAS), 'ativo': '1'}
        return self.client.post(url, {**base, **dados})

    def test_prontuario_trava_escopo_e_obrigatorio(self):
        resp = self._post(escopo='CATEGORIA', categoria='FACIAL', obrigatorio='1')
        self.assertEqual(resp.status_code, 302)
        form = FormularioAnamnese.objects.get(tipo='PRONTUARIO')
        self.assertEqual((form.escopo, form.categoria, form.obrigatorio), ('GLOBAL', '', False))

    def test_segundo_prontuario_ativo_e_recusado_com_mensagem(self):
        _questionario()
        resp = self._post(nome='Outro')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe um questionário do prontuário ativo')
        self.assertEqual(FormularioAnamnese.objects.filter(tipo='PRONTUARIO').count(), 1)

    def test_editar_label_no_lugar_nao_versiona_e_mantem_respostas(self):
        form = _questionario()
        Prontuario.objects.create(cliente=self.cli, respostas_extras={'fumante': True})
        novo = [{**PERGUNTAS[0], 'label': 'É fumante?'}]
        resp = self._post(pk=form.pk, schema_json=json.dumps(novo))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(FormularioAnamnese.objects.filter(tipo='PRONTUARIO').count(), 1)
        form.refresh_from_db()
        self.assertEqual(form.schema_json[0]['label'], 'É fumante?')
        self.assertEqual(Prontuario.objects.get(cliente=self.cli).respostas_extras, {'fumante': True})

    def test_lista_nao_oferece_respostas_avulsas_do_prontuario(self):
        form = _questionario()
        html = self.client.get(reverse('aranha:admin_anamneses')).content.decode()
        self.assertIn('Questionario do prontuario (equipe)', html)
        self.assertNotIn(reverse('aranha:admin_anamnese_respostas', args=[form.pk]), html)


# ─── Alertas de saude com o questionario unificado ─────────────────────
def _alertas_regra_antiga(perguntas_cfg, extras):
    """Copia congelada do trecho do utils.saude.alertas_saude @ 0050 (Configuracao)."""
    textos = {p.get('chave'): p.get('texto') for p in perguntas_cfg if isinstance(p, dict)}
    out = []
    for chave, valor in extras.items():
        label = str(textos.get(chave) or str(chave).replace('_', ' ').strip().capitalize())
        if not saude._RISCO_RX.search(saude._sem_acento(f'{chave} {label}')):
            continue
        texto = saude._valor_positivo(valor)
        if texto:
            out.append({'label': label, 'valor': texto, 'origem': 'prontuario'})
    return out


class AlertasQuestionarioTests(TestCase):
    CONFIG = [
        {'chave': 'esta_gravida_ou_amamentando', 'texto': 'Está grávida ou amamentando?', 'tipo': 'BOOLEAN'},
        {'chave': 'usa_anticoagulante', 'texto': 'Usa algum remédio para afinar o sangue?', 'tipo': 'BOOLEAN'},
        {'chave': 'fuma', 'texto': 'Fuma?', 'tipo': 'BOOLEAN'},
        {'chave': 'cirurgias_previas', 'texto': 'Cirurgias anteriores', 'tipo': 'TEXTO'},
        {'chave': 'observacao', 'texto': 'Tem alergia a algum cosmético?', 'tipo': 'TEXTO'},
        {'chave': 'sem_texto', 'tipo': 'TEXTO'},
    ]
    EXTRAS = {
        'esta_gravida_ou_amamentando': True, 'usa_anticoagulante': True, 'fuma': True,
        'cirurgias_previas': 'Rinoplastia 2020', 'observacao': 'Sim, a ácidos', 'sem_texto': 'qualquer',
        'doenca_cronica_x': 'Diabetes tipo 2', 'retinoide_x': False, 'medicamentos_x': 'nenhum',
    }

    def test_caracterizacao_dados_migrados_dao_os_mesmos_alertas(self):
        perguntas = M0051._converter(json.dumps(self.CONFIG))
        _questionario(perguntas)
        cli = criar_cliente()
        Prontuario.objects.create(cliente=cli, respostas_extras=self.EXTRAS)
        # mesma ordem de chaves que o banco devolve (jsonb do PG ordena as chaves)
        extras = Prontuario.objects.get(cliente=cli).respostas_extras
        esperado = _alertas_regra_antiga(self.CONFIG, extras)
        self.assertEqual(len(esperado), 5)
        self.assertEqual(alertas_saude(cli), esperado)

    def test_alerta_saude_true_e_false_sobrepoem_a_heuristica(self):
        _questionario([
            {'key': 'fuma', 'tipo': 'bool', 'label': 'Fuma?', 'alerta_saude': True},
            {'key': 'alergia_a_perfume', 'tipo': 'bool', 'label': 'Gosta de perfume?', 'alerta_saude': False},
            {'key': 'diabetes', 'tipo': 'bool', 'label': 'Diabetes?'},
        ])
        cli = criar_cliente()
        Prontuario.objects.create(cliente=cli, respostas_extras={
            'fuma': True, 'alergia_a_perfume': True, 'diabetes': True,
        })
        self.assertEqual({a['label'] for a in alertas_saude(cli)}, {'Fuma?', 'Diabetes?'})
        # resposta negativa nao alerta nem marcada
        Prontuario.objects.filter(cliente=cli).update(respostas_extras={'fuma': False})
        self.assertEqual(alertas_saude(cli), [])

    def test_alerta_saude_vale_tambem_na_ficha_de_anamnese(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        at = criar_atendimento(cli, prof, proc)
        form = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', schema_json=[
            {'key': 'usa_lente', 'tipo': 'bool', 'label': 'Usa lente de contato?', 'alerta_saude': True},
            {'key': 'alergia_sol', 'tipo': 'text', 'label': 'Alergia ao sol?', 'alerta_saude': False},
            {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?'},
        ])
        from django.utils import timezone
        RespostaAnamnese.objects.create(formulario=form, cliente=cli, atendimento=at, respondida_em=timezone.now(),
                                        respostas_json={'usa_lente': True, 'alergia_sol': 'sim', 'gestante': True})
        self.assertEqual(sorted((a['label'], a['origem']) for a in alertas_saude(cli)),
                         [('Está gestante?', 'anamnese'), ('Usa lente de contato?', 'anamnese')])

    def test_lote_le_as_perguntas_uma_vez(self):
        from aranha_estetica.services.alertas import alertas_por_cliente
        _questionario([{'key': 'fuma', 'tipo': 'bool', 'label': 'Fuma?', 'alerta_saude': True}])
        clientes = [criar_cliente() for _ in range(6)]
        for cli in clientes:
            Prontuario.objects.create(cliente=cli, respostas_extras={'fuma': True})
        # prontuarios + fichas + perguntas: o mesmo p/ 2 ou 6 clientes (antes 2 por cliente)
        for lote in (clientes[:2], clientes):
            with self.subTest(clientes=len(lote)), self.assertNumQueries(3):
                mapa = alertas_por_cliente(lote)
            self.assertEqual(set(mapa), {c.pk for c in lote})

    def test_lote_considera_as_5_fichas_mais_recentes_de_cada_cliente(self):
        from datetime import timedelta

        from django.utils import timezone

        from aranha_estetica.utils.saude import alertas_em_lote
        form = FormularioAnamnese.objects.create(
            nome='Anamnese', tipo='ANAMNESE', escopo='GLOBAL',
            schema_json=[{'key': 'alergia', 'tipo': 'text', 'label': 'Alergia?'}])
        muitas, uma = criar_cliente(), criar_cliente()
        agora = timezone.now()
        # a alergia so aparece na 6a ficha mais recente: fica de fora (limite 5 por cliente)
        for dias, valor in ((10, 'dipirona'), (5, 'nao'), (4, 'nao'), (3, 'nao'), (2, 'nao'), (1, 'nao')):
            RespostaAnamnese.objects.create(formulario=form, cliente=muitas, respostas_json={'alergia': valor},
                                            respondida_em=agora - timedelta(days=dias))
        RespostaAnamnese.objects.create(formulario=form, cliente=uma, respostas_json={'alergia': 'latex'},
                                        respondida_em=agora - timedelta(days=30))
        mapa = alertas_em_lote([muitas.pk, uma.pk])
        self.assertEqual(mapa, {uma.pk: [{'label': 'Alergia?', 'valor': 'latex', 'origem': 'anamnese'}]})
        self.assertEqual(alertas_saude(muitas, limite_fichas=6)[0]['valor'], 'dipirona')


class ConversaoDaMigracaoTests(TestCase):
    """0051._converter: o que vira formulario e o que fica na Configuracao."""

    def test_formato_convertido(self):
        perguntas = M0051._converter(json.dumps([
            {'chave': 'fuma', 'texto': ' Fuma? ', 'tipo': 'BOOLEAN'},
            {'chave': 'obs', 'texto': 'Obs', 'tipo': 'TEXTO'},
            {'chave': 'sem_texto_x'},
        ]))
        self.assertEqual(perguntas, [
            {'key': 'fuma', 'label': 'Fuma?', 'tipo': 'bool', 'obrigatorio': False},
            {'key': 'obs', 'label': 'Obs', 'tipo': 'longtext', 'obrigatorio': False},
            {'key': 'sem_texto_x', 'label': 'Sem texto x', 'tipo': 'longtext', 'obrigatorio': False},
        ])
        FormularioAnamnese(nome='F', tipo='PRONTUARIO', schema_json=perguntas).full_clean()

    def test_invalidos(self):
        for valor in ('{nao e json', '{"chave": "x"}', '[1, 2]', '[{"texto": "sem chave"}]',
                      '[{"chave": 3}]', '[{"chave": "a"}, {"chave": "a"}]'):
            with self.subTest(valor=valor):
                self.assertIsNone(M0051._converter(valor))
        self.assertEqual(M0051._converter('[]'), [])

    def test_seed_cria_o_questionario_uma_vez(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        with mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': '', 'DJANGO_ENV': ''}):
            call_command('seed', stdout=StringIO())
            call_command('seed', stdout=StringIO())
        form = FormularioAnamnese.objects.get(tipo='PRONTUARIO')
        self.assertTrue(form.ativo)
        form.full_clean()
        self.assertTrue(re.match(r'^Question', form.nome))
