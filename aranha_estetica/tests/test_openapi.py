"""Contrato da API (aranha_estetica/api/openapi.yaml) conferido contra o codigo.

O openapi.yaml e escrito a mao (descricoes, codigos de erro, exemplos); estes testes impedem que ele
envelheca em silencio:
  - e um OpenAPI 3.1 valido (openapi-spec-validator: estrutura e `$ref`);
  - cada caminho resolve, no URLconf, para o `x-url-name` declarado; operacao com resumo, tag
    existente e operationId unico;
  - toda view que responde JSON, ICS ou o export LGPD (marcas no corpo da view ou de um helper do
    mesmo modulo que ela chama) esta documentada — rota JSON nova sem entrada reprova aqui;
  - o enum de jobs do cron e exatamente jobs.JOB_MAP;
  - a equipe abre /painel/api/ e baixa o YAML; anonimo e profissional nao.
"""
import inspect
import re

import yaml
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse
from openapi_spec_validator import validate

from aranha_estetica.jobs import JOB_MAP
from aranha_estetica.models import Usuario
from aranha_estetica.views.painel.api import ESPECIFICACAO

from .factories import criar_profissional

METODOS = ('get', 'post', 'put', 'patch', 'delete')
# Resposta de maquina: envelope de utils/respostas, JsonResponse cru (webhooks, saude, calendario),
# feed ICS e o download do export LGPD.
MARCAS = ('json_ok(', 'json_erro(', 'json_recusa(', 'JsonResponse(', 'text/calendar', 'resposta_export(')
PARAMETRO = re.compile(r'\{([^}]+)\}')


def _especificacao():
    return yaml.safe_load(ESPECIFICACAO.read_text(encoding='utf-8'))


def _padroes(resolver=None, prefixo=''):
    """(nome qualificado, callback) de cada rota nomeada do projeto."""
    resolver = resolver or get_resolver()
    for p in resolver.url_patterns:
        if isinstance(p, URLResolver):
            ns = f'{prefixo}{p.namespace}:' if p.namespace else prefixo
            yield from _padroes(p, ns)
        elif isinstance(p, URLPattern) and p.name:
            yield f'{prefixo}{p.name}', p.callback


def _fontes(callback):
    """Codigo da view (sem decoradores) + o dos helpers do mesmo modulo que ela chama."""
    funcao = inspect.unwrap(callback)
    modulo = inspect.getmodule(funcao)
    if modulo is None or not modulo.__name__.startswith('aranha_estetica.'):
        return []   # views genericas do Django (TemplateView dos manifests, reset de senha)
    fontes = [inspect.getsource(funcao)]
    for nome in funcao.__code__.co_names:
        helper = getattr(modulo, nome, None)
        if inspect.isfunction(helper) and helper.__module__ == modulo.__name__:
            fontes.append(inspect.getsource(helper))
    return fontes


def _resolvido(spec, no):
    """Segue o `$ref` local (#/components/...) ate o objeto apontado."""
    while '$ref' in no:
        alvo = spec
        for parte in no['$ref'].removeprefix('#/').split('/'):
            alvo = alvo[parte]
        no = alvo
    return no


def _exemplo(spec, operacoes, nome):
    """Valor de exemplo do parametro de caminho `nome` (o `example` declarado ou um generico)."""
    for op in operacoes:
        for param in op.get('parameters', []):
            param = _resolvido(spec, param)
            if param.get('in') == 'path' and param.get('name') == nome:
                if 'example' in param:
                    return str(param['example'])
                return '1' if param.get('schema', {}).get('type') == 'integer' else 'x'
    return 'x'


class EspecificacaoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.spec = _especificacao()

    def _operacoes(self, item):
        return [item[m] for m in METODOS if m in item]

    def test_openapi_31_valido(self):
        self.assertTrue(self.spec['openapi'].startswith('3.1'))
        validate(self.spec)

    def test_caminho_resolve_para_a_rota_declarada(self):
        for caminho, item in self.spec['paths'].items():
            with self.subTest(caminho=caminho):
                self.assertIn('x-url-name', item, 'declare x-url-name (nome da rota no Django)')
                concreto = PARAMETRO.sub(lambda m: _exemplo(self.spec, self._operacoes(item), m.group(1)), caminho)
                self.assertEqual(resolve(concreto).view_name, item['x-url-name'])

    def test_operacoes_com_resumo_tag_e_operation_id_unico(self):
        tags = {t['name'] for t in self.spec['tags']}
        vistos = set()
        for caminho, item in self.spec['paths'].items():
            for op in self._operacoes(item):
                with self.subTest(caminho=caminho, operacao=op.get('operationId')):
                    self.assertTrue(op.get('summary'))
                    self.assertTrue(op.get('tags'))
                    self.assertLessEqual(set(op['tags']), tags, 'tag fora da lista `tags`')
                    self.assertNotIn(op['operationId'], vistos)
                    vistos.add(op['operationId'])

    def test_toda_rota_de_maquina_esta_documentada(self):
        documentadas = {item['x-url-name'] for item in self.spec['paths'].values()}
        de_maquina = {nome for nome, callback in _padroes()
                      if any(marca in fonte for fonte in _fontes(callback) for marca in MARCAS)}
        self.assertGreater(len(de_maquina), 20)   # a deteccao continua enxergando as views
        self.assertEqual(sorted(de_maquina - documentadas), [],
                         'rota JSON/ICS sem entrada em aranha_estetica/api/openapi.yaml')

    def test_jobs_do_cron_sao_os_do_job_map(self):
        self.assertEqual(self.spec['components']['schemas']['Job']['enum'], sorted(JOB_MAP))


class DocumentacaoNoPainelTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(email='admin@api.test', password='x', papel=Usuario.PAPEL_ADMIN)
        self.prof = Usuario.objects.create_user(
            email='prof@api.test', password='x', papel=Usuario.PAPEL_PROFISSIONAL,
            profissional=criar_profissional('Jaqueline'))

    def test_equipe_abre_o_swagger_e_baixa_o_yaml(self):
        self.client.force_login(self.admin)
        tela = self.client.get(reverse('aranha:painel_api'))
        self.assertContains(tela, f'data-url-spec="{reverse("aranha:painel_api_spec")}"')
        resp = self.client.get(reverse('aranha:painel_api_spec'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/yaml; charset=utf-8')
        self.assertEqual(yaml.safe_load(resp.content)['info']['title'], _especificacao()['info']['title'])

    def test_anonimo_e_profissional_nao_veem_a_especificacao(self):
        for usuario in (None, self.prof):
            with self.subTest(usuario=usuario):
                self.client.logout()
                if usuario:
                    self.client.force_login(usuario)
                for rota in ('aranha:painel_api', 'aranha:painel_api_spec'):
                    resp = self.client.get(reverse(rota))
                    self.assertEqual(resp.status_code, 302)
                    self.assertNotIn(b'openapi', resp.content)
