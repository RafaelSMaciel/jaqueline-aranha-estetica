"""Seed do ambiente para Jaqueline Aranha Estetica.

Cria unica profissional ativa (Jaqueline Aranha — biomedica), desativa outras,
substitui procedimentos pelos 23 da lista oficial com precos base.

Uso:
    python manage.py seed_jaqueline           # cria / atualiza
    python manage.py seed_jaqueline --reset   # apaga procedimentos antigos antes
"""
from datetime import date, time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from app_shivazen.models import (
    Atendimento, DisponibilidadeProfissional, Preco, Procedimento,
    Profissional, ProfissionalProcedimento,
)


PROCEDIMENTOS = [
    # (nome, categoria, duracao_min, descricao_curta, preco_base)
    ('Botox 1 região', 'FACIAL', 30, 'Aplicação em uma região (testa, glabela ou pés-de-galinha).', '300.00'),
    ('Botox 2 regiões', 'FACIAL', 45, 'Aplicação em duas regiões combinadas.', '600.00'),
    ('Botox 3 regiões', 'FACIAL', 60, 'Aplicação em três regiões combinadas.', '900.00'),
    ('Preenchimento Labial', 'FACIAL', 60, 'Ácido hialurônico para volume e contorno labial.', '999.90'),
    ('Preenchimento Bigode Chinês', 'FACIAL', 60, 'Preenchimento dos sulcos nasolabiais.', '899.90'),
    ('Preenchimento Glúteos com ativos', 'CORPORAL', 90, 'Preenchimento glúteo com ativos biocompatíveis.', '2300.00'),
    ('Preenchimento de Malar', 'FACIAL', 60, 'Preenchimento da região zigomática (maçã do rosto).', '899.90'),
    ('Bioestimulador de Colágeno', 'FACIAL', 60, 'Estímulo à produção natural de colágeno.', '1200.00'),
    ('Rinomodelação', 'FACIAL', 60, 'Harmonização nasal sem cirurgia via ácido hialurônico.', '1100.00'),
    ('PRP (Fibrina)', 'FACIAL', 60, 'Plasma rico em plaquetas — valor por sessão.', '300.00'),
    ('Preenchimento com PRP', 'FACIAL', 60, 'Preenchimento associado a PRP — valor por sessão.', '450.00'),
    ('Enzimas para gordura localizada', 'CORPORAL', 45, 'Aplicação enzimática para gordura localizada — por sessão.', '120.00'),
    ('Enzimas para flacidez', 'CORPORAL', 45, 'Aplicação enzimática para flacidez — por sessão.', '120.00'),
    ('Enzimas para estrias', 'CORPORAL', 45, 'Aplicação enzimática para estrias — por sessão.', '120.00'),
    ('Skinbooster', 'FACIAL', 60, 'Hidratação profunda com ácido hialurônico — por sessão.', '150.00'),
    ('Microagulhamento', 'FACIAL', 60, 'Indução percutânea de colágeno — por sessão.', '150.00'),
    ('Drenagem Linfática Corporal', 'CORPORAL', 60, 'Drenagem manual corporal.', '120.00'),
    ('Drenagem Linfática Facial', 'FACIAL', 30, 'Drenagem manual facial.', '60.00'),
    ('Endermoterapia', 'CORPORAL', 30, 'Massagem mecânica subcutânea.', '60.00'),
    ('Limpeza de Pele', 'FACIAL', 60, 'Limpeza profunda e extração de impurezas.', '130.00'),
    ('Ventosaterapia', 'CORPORAL', 30, 'Terapia com ventosas — relaxamento e circulação.', '80.00'),
    ('Massagem Relaxante', 'CORPORAL', 60, 'Massagem corporal relaxante.', '90.00'),
    ('Depilação a Laser', 'CORPORAL', 30, 'Depilação a laser diodo. Consultar região e sessão.', '0.00'),
]


class Command(BaseCommand):
    help = 'Seed para Jaqueline Aranha Estetica (biomedica unica + procedimentos oficiais).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Apaga procedimentos/precos antigos antes de criar.',
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        self.stdout.write(self.style.NOTICE('=== Seed Jaqueline Aranha Estetica ==='))

        # 1. Profissional unica — biomedica Jaqueline Aranha (renomeia se existir
        #    registro anterior com 'Dra. Jaqueline Aranha').
        prof = (
            Profissional.objects.filter(nome__in=['Dra. Jaqueline Aranha', 'Jaqueline Aranha'])
            .first()
        )
        created = prof is None
        if prof is None:
            prof = Profissional(nome='Jaqueline Aranha')
        else:
            prof.nome = 'Jaqueline Aranha'
        prof.especialidade = 'Biomédica · Estética facial e corporal · Injetáveis'
        prof.ativo = True
        prof.save()
        self.stdout.write(self.style.SUCCESS(
            f'{"Criada" if created else "Atualizada"} profissional pk={prof.pk}'
        ))

        # 2. Desativa outras profissionais (mantem historico; nao deleta)
        outras = Profissional.objects.exclude(pk=prof.pk)
        desativadas = outras.filter(ativo=True).update(ativo=False)
        self.stdout.write(self.style.WARNING(
            f'{desativadas} outra(s) profissional(is) desativada(s).'
        ))

        # 3. Reset procedimentos (se flag)
        if opts['reset']:
            # Soft reset: marca inativos para preservar FKs em atendimentos
            atendimento_procs = set(
                Atendimento.objects.values_list('procedimento_id', flat=True).distinct()
            )
            deletaveis = Procedimento.objects.exclude(pk__in=atendimento_procs)
            count_del = deletaveis.count()
            deletaveis.delete()
            Procedimento.objects.filter(pk__in=atendimento_procs).update(ativo=False)
            self.stdout.write(self.style.WARNING(
                f'Reset: {count_del} procedimento(s) deletado(s); '
                f'{len(atendimento_procs)} com historico desativado(s).'
            ))

        # 4. Cria/atualiza procedimentos + precos + vinculos
        criados = atualizados = 0
        for nome, categoria, duracao, descricao, preco_str in PROCEDIMENTOS:
            proc, foi_criado = Procedimento.objects.get_or_create(
                nome=nome,
                defaults={
                    'categoria': categoria,
                    'duracao_minutos': duracao,
                    'descricao': descricao,
                    'ativo': True,
                },
            )
            if foi_criado:
                criados += 1
            else:
                proc.categoria = categoria
                proc.duracao_minutos = duracao
                proc.descricao = descricao
                proc.ativo = True
                proc.save()
                atualizados += 1

            # Vincula ao profissional
            ProfissionalProcedimento.objects.get_or_create(
                profissional=prof, procedimento=proc,
            )

            # Preco base (profissional=None → preco generico)
            Preco.objects.update_or_create(
                procedimento=proc, profissional=None,
                defaults={
                    'valor': Decimal(preco_str),
                    'vigente_desde': date.today(),
                    'descricao': 'Tabela base' if preco_str != '0.00' else 'A consultar',
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f'Procedimentos: {criados} criado(s), {atualizados} atualizado(s).'
        ))

        # 5. Disponibilidade semanal padrao (Seg-Sex 9h-18h, Sab 9h-15h)
        # dia_semana: 1=Dom, 2=Seg, ..., 7=Sab
        padroes = [
            (2, time(9, 0), time(18, 0)),   # Segunda
            (3, time(9, 0), time(18, 0)),   # Terca
            (4, time(9, 0), time(18, 0)),   # Quarta
            (5, time(9, 0), time(18, 0)),   # Quinta
            (6, time(9, 0), time(18, 0)),   # Sexta
            (7, time(9, 0), time(15, 0)),   # Sabado
        ]
        for dia, ini, fim in padroes:
            DisponibilidadeProfissional.objects.update_or_create(
                profissional=prof, dia_semana=dia, hora_inicio=ini,
                defaults={'hora_fim': fim},
            )
        self.stdout.write(self.style.SUCCESS(
            'Disponibilidade Seg-Sex 9h-18h, Sab 9h-15h.'
        ))

        # 6. Remove vinculos e disponibilidades de profissionais desativadas
        DisponibilidadeProfissional.objects.exclude(profissional=prof).delete()
        ProfissionalProcedimento.objects.exclude(profissional=prof).delete()
        self.stdout.write(self.style.SUCCESS(
            'Disponibilidades/vinculos de outras profissionais removidos.'
        ))

        self.stdout.write(self.style.SUCCESS('=== Seed concluido ==='))
