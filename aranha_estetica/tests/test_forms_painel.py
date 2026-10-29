"""forms/painel.py: a validacao das telas de procedimento, bloqueio e termo saiu das views."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.forms.cadastros import ProcedimentoForm
from aranha_estetica.forms.painel import BloqueioForm, TermoForm, parse_preco

from .factories import criar_procedimento, criar_profissional


class ProcedimentoFormTests(TestCase):
    def test_preco_com_virgula_e_padroes(self):
        form = ProcedimentoForm({'nome': ' Drenagem ', 'preco': '1350,5', 'categoria': 'xx'})
        self.assertTrue(form.is_valid(), form.errors)
        d = form.cleaned_data
        self.assertEqual((d['nome'], d['preco'], d['duracao_minutos'], d['categoria']),
                         ('Drenagem', Decimal('1350.50'), 30, 'OUTRO'))
        self.assertFalse(d['ativo'])

    def test_edicao_mantem_duracao_e_categoria(self):
        proc = criar_procedimento(duracao=45, categoria='FACIAL')
        form = ProcedimentoForm({'nome': 'X', 'categoria': 'nao-existe', 'ativo': '1'}, instance=proc)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data['duracao_minutos'], form.cleaned_data['categoria']), (45, 'FACIAL'))
        self.assertTrue(form.cleaned_data['ativo'])

    def test_recusas_com_a_mensagem_da_tela(self):
        for dados in ({'nome': 'X', 'duracao_minutos': '2'}, {'nome': 'X', 'preco': '1e30'},
                      {'nome': 'X', 'duracao_minutos': 'abc'}):
            form = ProcedimentoForm(dados)
            self.assertFalse(form.is_valid())
            self.assertIn('Duração ou preço inválido.', sum(form.errors.values(), []))
        form = ProcedimentoForm({'nome': '   '})
        self.assertFalse(form.is_valid())
        self.assertIn('Informe o nome do procedimento.', form.errors['nome'])

    def test_parse_preco_sem_form(self):
        self.assertIsNone(parse_preco(''))
        with self.assertRaises(ValueError):
            parse_preco('-1')


class BloqueioFormTests(TestCase):
    def _dados(self, **extra):
        inicio = timezone.localtime(timezone.now() + timedelta(days=2)).replace(hour=12, minute=0, second=0,
                                                                               microsecond=0)
        dados = {'profissional_id': 'todos', 'data_hora_inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
                 'data_hora_fim': (inicio + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'), 'motivo': ' Reforma '}
        dados.update(extra)
        return dados

    def test_global_e_aware(self):
        form = BloqueioForm(self._dados())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['profissional_id'])
        self.assertTrue(timezone.is_aware(form.cleaned_data['data_hora_inicio']))
        bloqueio = form.criar(None)
        self.assertEqual((bloqueio.tipo, bloqueio.motivo), ('BLOQUEIO', 'Reforma'))

    def test_profissional_e_fim_antes_do_inicio(self):
        prof = criar_profissional()
        self.assertTrue(BloqueioForm(self._dados(profissional_id=str(prof.pk))).is_valid())
        self.assertFalse(BloqueioForm(self._dados(profissional_id='999999')).is_valid())
        self.assertFalse(BloqueioForm(self._dados(profissional_id='²')).is_valid())
        dados = self._dados()
        dados['data_hora_fim'] = dados['data_hora_inicio']
        form = BloqueioForm(dados)
        self.assertFalse(form.is_valid())
        self.assertIn('Data fim deve ser posterior a data inicio.', form.non_field_errors())


class TermoFormTests(TestCase):
    def test_padroes(self):
        form = TermoForm({'titulo': ' Política ', 'conteudo': 'Texto'})
        self.assertTrue(form.is_valid(), form.errors)
        d = form.cleaned_data
        self.assertEqual((d['tipo'], d['versao'], d['titulo'], d['procedimento']), ('LGPD', '1.0', 'Política', None))
        self.assertEqual(d['vigente_desde'], timezone.localdate())

    def test_recusas(self):
        casos = {
            'Tipo de termo inválido.': {'tipo': 'X', 'titulo': 'a', 'conteudo': 'b'},
            'Informe título e conteúdo do termo.': {'titulo': 'a'},
            'Versão deve ter no máximo 20 caracteres.': {'titulo': 'a', 'conteudo': 'b', 'versao': 'v' * 21},
            'Procedimento inválido.': {'tipo': 'PROCEDIMENTO', 'titulo': 'a', 'conteudo': 'b', 'procedimento_id': '²'},
            'Procedimento não encontrado.': {'tipo': 'PROCEDIMENTO', 'titulo': 'a', 'conteudo': 'b',
                                             'procedimento_id': '999999'},
            'Data de vigência inválida.': {'titulo': 'a', 'conteudo': 'b', 'vigente_desde': '31/12/2026'},
        }
        for mensagem, dados in casos.items():
            with self.subTest(mensagem):
                form = TermoForm(dados)
                self.assertFalse(form.is_valid())
                self.assertIn(mensagem, sum(form.errors.values(), []))

    def test_termo_de_procedimento(self):
        proc = criar_procedimento()
        form = TermoForm({'tipo': 'PROCEDIMENTO', 'titulo': 'a', 'conteudo': 'b', 'procedimento_id': str(proc.pk)})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['procedimento'], proc)
