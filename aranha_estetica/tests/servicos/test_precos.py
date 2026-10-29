"""Preco de vitrine (services/precos): a mesma conta do agendamento, em lote e com consultas fixas."""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from aranha_estetica.models import Preco, Promocao
from aranha_estetica.services.precos import PrecoExibido, precos_de_vitrine
from aranha_estetica.utils import datas
from aranha_estetica.utils.precos import melhor_promocao, preco_com_promocao

from ..factories import criar_procedimento, criar_profissional


def _promo(nome, procedimento=None, **campos):
    hoje = datas.hoje()
    return Promocao.objects.create(nome=nome, procedimento=procedimento, data_inicio=hoje,
                                   data_fim=hoje + timedelta(days=5), **campos)


class PrecosDeVitrineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.proc = criar_procedimento(nome='Drenagem', preco=Decimal('100.00'))

    def test_sem_promocao(self):
        self.assertEqual(precos_de_vitrine([self.proc]),
                         {self.proc.pk: PrecoExibido(final=Decimal('100.00'), cheio=Decimal('100.00'))})

    def test_vence_a_promocao_de_menor_valor_e_bate_com_o_agendamento(self):
        _promo('Geral 10', desconto_percentual=Decimal('10'))
        melhor = _promo('Fixa 70', self.proc, preco_promocional=Decimal('70.00'))
        preco = precos_de_vitrine([self.proc])[self.proc.pk]
        self.assertEqual((preco.final, preco.promocao, preco.em_promocao), (Decimal('70.00'), melhor, True))
        self.assertEqual(preco_com_promocao(self.proc)[:2], (preco.final, preco.promocao))

    def test_promocao_que_nao_reduz_aparece_sem_promocao(self):
        _promo('Mais cara', self.proc, preco_promocional=Decimal('120.00'))
        preco = precos_de_vitrine([self.proc])[self.proc.pk]
        self.assertEqual((preco.final, preco.promocao, preco.em_promocao), (Decimal('100.00'), None, False))

    def test_sem_preco_fica_fora(self):
        sem = criar_procedimento(nome='Avaliação', preco=None)
        self.assertNotIn(sem.pk, precos_de_vitrine([self.proc, sem]))

    def test_percentual_sobre_o_preco_do_profissional_sem_base(self):
        prof = criar_profissional()
        proc = criar_procedimento(nome='Laser', preco=None, profissional=prof)
        Preco.objects.create(procedimento=proc, profissional=prof, valor=Decimal('1000.00'))
        _promo('Vinte', proc, desconto_percentual=Decimal('20'))
        self.assertEqual(precos_de_vitrine([proc])[proc.pk].final, Decimal('800.00'))

    def test_consultas_fixas_para_qualquer_catalogo(self):
        _promo('Geral', desconto_percentual=Decimal('5'))
        um = [self.proc]
        vinte = um + [criar_procedimento(nome=f'Proc {i}', preco=Decimal('50.00')) for i in range(19)]
        for i, proc in enumerate(vinte[::3]):
            _promo(f'Promo {i}', proc, desconto_percentual=Decimal('10'))
        with self.assertNumQueries(2):
            precos_de_vitrine(um)
        with self.assertNumQueries(2):
            self.assertEqual(len(precos_de_vitrine(vinte)), 20)


class MelhorPromocaoTests(TestCase):
    def test_funcao_pura(self):
        proc = criar_procedimento(nome='Peeling', preco=None)
        fixa = _promo('Fixa', proc, preco_promocional=Decimal('60.00'))
        pct = _promo('Pct', proc, desconto_percentual=Decimal('50'))
        self.assertIsNone(melhor_promocao([], Decimal('100')))
        self.assertEqual(melhor_promocao([fixa, pct], Decimal('100')), pct)
        self.assertEqual(melhor_promocao([fixa, pct], None), fixa)   # sem preco: so a de preco fixo
        self.assertIsNone(melhor_promocao([pct], Decimal('0')))      # nada a reduzir


class MesmoAPartirDeEmTodaVitrineTests(TestCase):
    """Cards do wizard, widget, especialidades e pagina do servico mostram o mesmo valor."""

    def test_mesmo_valor_nas_quatro_telas(self):
        cache.clear()
        prof = criar_profissional()
        proc = criar_procedimento(nome='Drenagem Glow', categoria='CORPORAL', preco=Decimal('200.00'), profissional=prof)
        _promo('Semana', proc, desconto_percentual=Decimal('25'))
        card = next(c for c in self.client.get(reverse('aranha:agendamento_publico')).context['procedimentos']
                    if c['id'] == proc.pk)
        self.assertEqual((card['preco'], card['preco_cheio'], card['promocao']), (150.0, 200.0, 'Semana'))
        embed = self.client.get(reverse('aranha:embed_agendar')).context['procedimentos']
        self.assertEqual(embed[0]['preco'], 150.0)
        self.assertContains(self.client.get(reverse('aranha:especialidades')), 'A partir de R$ 150')
        proc.refresh_from_db()
        self.assertContains(self.client.get(reverse('aranha:servico_detalhe', args=[proc.slug])), 'A partir de R$ 150')
