"""AgendamentoPublicoForm: o POST do wizard vira PedidoAgendamento (sem cliente HTTP)."""
from datetime import timedelta

from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, TestCase
from django.utils import timezone

from aranha_estetica.forms.agendamento import (
    MSG_CAMPOS, MSG_IDADE, MSG_POLITICA, MSG_SELECAO, MSG_SEM_OTP, AgendamentoPublicoForm,
)

from ..factories import criar_procedimento, criar_profissional


class AgendamentoPublicoFormTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.request = RequestFactory().post('/agendamento/confirmar/')
        self.request.session = SessionStore()
        self.request.session['cliente_telefone'] = '17988887777'
        self.request.session['cliente_verificado_em'] = (timezone.now() - timedelta(minutes=1)).isoformat()

    def _form(self, **campos):
        dados = {
            'nome': '  Dora Teste  ', 'telefone': '(17) 98888-7777', 'data_nascimento': '1990-06-15',
            'email': ' Dora@Exemplo.TEST ', 'procedimento': str(self.proc.pk), 'profissional': str(self.prof.pk),
            'datetime': (timezone.now() + timedelta(days=3)).isoformat(), 'aceite_politica': 'on',
        }
        dados.update(campos)
        return AgendamentoPublicoForm(dados, request=self.request)

    def test_pedido_limpo(self):
        form = self._form(consent_whatsapp_nps='on', consents_sincronizados='1', aceite_termo_7='on',
                          aceite_termo_x='on', aceite_termo_9='', anamnese_respostas='{"3": {"a": "b"}}')
        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.pedido()
        self.assertEqual((pedido.nome, pedido.telefone, pedido.email), ('Dora Teste', '17988887777', 'dora@exemplo.test'))
        self.assertEqual((pedido.procedimento, pedido.profissional), (self.proc, self.prof))
        # opt-ins de comunicacao sairam do agendamento (2026-11-24): o form nem le os campos antigos
        self.assertFalse(hasattr(pedido, 'consents'))
        self.assertEqual(pedido.extras, ())
        self.assertEqual(len(pedido.itens), 1)
        self.assertEqual(pedido.termos_marcados, frozenset({7}))
        self.assertEqual(pedido.anamneses, {'3': {'a': 'b'}})
        self.assertFalse(pedido.consent_saude)

    def test_itens_extras_viram_os_outros_tratamentos_do_pedido(self):
        import json
        outro = criar_procedimento('Drenagem', profissional=self.prof)
        quando = (timezone.localtime() + timedelta(days=4)).replace(hour=14, minute=0, second=0, microsecond=0)
        form = self._form(itens_extras=json.dumps([
            {'procedimento': outro.pk, 'profissional': self.prof.pk, 'datetime': quando.isoformat()},
        ]))
        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.pedido()
        self.assertEqual([item.procedimento for item in pedido.itens], [self.proc, outro])
        self.assertEqual(pedido.extras[0].inicio, quando)

    def test_itens_extras_invalido_recusa_a_selecao(self):
        for lixo in ('{', '[1]', '[{"procedimento": 0}]', '[' + ','.join(['{}'] * 5) + ']'):
            with self.subTest(lixo=lixo):
                self.assertEqual(self._form(itens_extras=lixo).primeiro_erro(), MSG_SELECAO)

    def test_campo_vazio_vem_antes_dos_outros_erros(self):
        form = self._form(nome='', aceite_politica='', telefone='123')
        self.assertFalse(form.is_valid())
        self.assertEqual(form.primeiro_erro(), MSG_CAMPOS)

    def test_ordem_dos_erros(self):
        self.assertEqual(self._form(aceite_politica='', procedimento='abc').primeiro_erro(), MSG_POLITICA)
        self.assertEqual(self._form(telefone='17911112222', procedimento='abc').primeiro_erro(), MSG_SEM_OTP)
        hoje = timezone.localdate()
        self.assertEqual(self._form(data_nascimento=hoje.replace(year=hoje.year - 17).isoformat()).primeiro_erro(),
                         MSG_IDADE)
        self.assertEqual(self._form(datetime='lixo').primeiro_erro(), MSG_SELECAO)

    def test_checkbox_so_conta_com_o_valor_do_navegador(self):
        form = self._form(consent_dados_saude='off')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.pedido().consent_saude)
        marcado = self._form(consent_dados_saude='on')
        self.assertTrue(marcado.is_valid(), marcado.errors)
        self.assertTrue(marcado.pedido().consent_saude)
