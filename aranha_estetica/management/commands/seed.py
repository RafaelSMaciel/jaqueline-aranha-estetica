"""Popula um banco novo (dev/staging) com dados coerentes com os models atuais.

Idempotente e nao-destrutivo: so cria o que falta (get_or_create) e nunca
sobrescreve procedimento, preco ou agenda que ja existam. Nao cria usuarios
nem senhas — o admin real vem de `manage.py bootstrap_admin`.

    python manage.py seed           # catalogo real, profissional, agenda, termo LGPD,
                                    # anamnese padrao e feriados
    python manage.py seed --demo    # + clientes e atendimentos ficticios (nunca em producao)
    python manage.py seed --force   # permite rodar em producao (sem --demo)
"""
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento, Cliente, DisponibilidadeProfissional, FormularioAnamnese,
    Habilitacao, Preco, Procedimento, Profissional, VersaoTermo,
)

PROFISSIONAL_NOME = 'Jaqueline Aranha'
PROFISSIONAL_ESPECIALIDADE = 'Biomédica · Estética facial e corporal · Injetáveis'

# Catalogo real informado pela clinica: (nome, categoria, duracao_min, descricao, preco_base | None)
CATALOGO = [
    ('Botox 1 região', 'FACIAL', 30, 'Aplicação em uma região (testa, glabela ou pés de galinha).', '300.00'),
    ('Botox 2 regiões', 'FACIAL', 45, 'Aplicação em duas regiões combinadas.', '600.00'),
    ('Botox 3 regiões', 'FACIAL', 60, 'Aplicação em três regiões combinadas.', '900.00'),
    ('Preenchimento Labial', 'FACIAL', 60, 'Ácido hialurônico para volume e contorno labial.', '999.90'),
    ('Preenchimento Bigode Chinês', 'FACIAL', 60, 'Suavização dos sulcos nasolabiais.', '899.90'),
    ('Preenchimento de Malar', 'FACIAL', 60, 'Realce da região das maçãs do rosto.', '899.90'),
    ('Preenchimento Glúteo com Ativos', 'CORPORAL', 90, 'Preenchimento glúteo com ativos biocompatíveis.', '2300.00'),
    ('Bioestimulador de Colágeno', 'FACIAL', 60, 'Estímulo à produção natural de colágeno.', '1200.00'),
    ('Rinomodelação', 'FACIAL', 60, 'Harmonização do nariz sem cirurgia, com ácido hialurônico.', '1100.00'),
    ('PRP (Fibrina)', 'FACIAL', 60, 'Plasma rico em plaquetas — valor por sessão.', '300.00'),
    ('Preenchimento com PRP', 'FACIAL', 60, 'Preenchimento associado ao PRP — valor por sessão.', '450.00'),
    ('Enzimas para Gordura Localizada', 'CORPORAL', 45, 'Aplicação de enzimas — valor por sessão.', '120.00'),
    ('Enzimas para Flacidez', 'CORPORAL', 45, 'Aplicação de enzimas — valor por sessão.', '120.00'),
    ('Enzimas para Estrias', 'CORPORAL', 45, 'Aplicação de enzimas — valor por sessão.', '120.00'),
    ('Skinbooster', 'FACIAL', 60, 'Hidratação profunda com ácido hialurônico — valor por sessão.', '150.00'),
    ('Microagulhamento', 'FACIAL', 60, 'Indução de colágeno — valor por sessão.', '150.00'),
    ('Limpeza de Pele', 'FACIAL', 60, 'Limpeza profunda e extração de impurezas.', '130.00'),
    ('Drenagem Linfática Corporal', 'CORPORAL', 60, 'Drenagem manual corporal.', '120.00'),
    ('Drenagem Linfática Facial', 'FACIAL', 30, 'Drenagem manual facial.', '60.00'),
    ('Endermoterapia', 'CORPORAL', 30, 'Massagem mecânica que ativa a circulação.', '60.00'),
    ('Ventosaterapia', 'CORPORAL', 30, 'Terapia com ventosas para relaxamento e circulação.', '80.00'),
    ('Massagem Relaxante', 'CORPORAL', 60, 'Massagem corporal relaxante.', '90.00'),
    ('Depilação a Laser', 'CORPORAL', 30, 'Depilação a laser. Região e valor sob consulta.', None),
]

# dia_semana: 1=Dom, 2=Seg, ..., 7=Sab
AGENDA_SEMANAL = [(dia, time(9, 0), time(18, 0)) for dia in range(2, 7)] + [(7, time(9, 0), time(15, 0))]

ANAMNESE_PADRAO = [
    {'key': 'gestante_ou_amamentando', 'tipo': 'bool', 'label': 'Está gestante ou amamentando?', 'obrigatorio': True},
    {'key': 'alergias', 'tipo': 'text', 'label': 'Tem alergia a algum produto, cosmético ou medicamento?', 'obrigatorio': False},
    {'key': 'uso_de_acidos', 'tipo': 'bool', 'label': 'Está usando ácidos ou retinoides na pele?', 'obrigatorio': False},
    {'key': 'procedimentos_recentes', 'tipo': 'longtext', 'label': 'Fez algum procedimento estético nos últimos 6 meses? Qual?', 'obrigatorio': False},
]

CLIENTES_DEMO = [
    ('Cliente Demonstração Um', '11900000001', 'demo1@exemplo.com'),
    ('Cliente Demonstração Dois', '11900000002', 'demo2@exemplo.com'),
    ('Cliente Demonstração Três', '11900000003', 'demo3@exemplo.com'),
]


def _em_producao() -> bool:
    return bool(os.environ.get('RAILWAY_ENVIRONMENT_NAME')) or \
        os.environ.get('DJANGO_ENV', '').strip().lower() == 'prod'


class Command(BaseCommand):
    help = 'Popula o banco com o catálogo real e dados de apoio (idempotente; dev/staging).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Permite rodar em produção (RAILWAY_ENVIRONMENT_NAME/DJANGO_ENV=prod).',
        )
        parser.add_argument(
            '--demo', action='store_true',
            help='Cria também clientes e atendimentos fictícios (recusado em produção).',
        )

    def handle(self, *args, **options):
        if _em_producao():
            if options['demo']:
                raise CommandError('Dados fictícios (--demo) nunca são criados em produção.')
            if not options['force']:
                raise CommandError('Recusando rodar seed em produção. Use --force se tem certeza.')

        with transaction.atomic():
            prof = self._profissional()
            procs = self._catalogo(prof)
            self._agenda(prof)
            self._termo_lgpd()
            self._anamnese()
            if options['demo']:
                self._demo(prof, procs)

        saida = StringIO()
        call_command('carregar_feriados', stdout=saida)
        linhas = [ln for ln in saida.getvalue().splitlines() if ln.startswith('Feriados:')]
        if linhas:
            self.stdout.write(linhas[-1])
        self.stdout.write(self.style.SUCCESS('Seed concluído.'))

    # ── partes ──────────────────────────────────────────────────────────

    def _profissional(self):
        prof = Profissional.objects.filter(
            nome__in=[PROFISSIONAL_NOME, f'Dra. {PROFISSIONAL_NOME}'],
        ).order_by('pk').first()
        if prof is None:
            prof = Profissional.objects.create(
                nome=PROFISSIONAL_NOME, especialidade=PROFISSIONAL_ESPECIALIDADE, ativo=True,
            )
            self.stdout.write(f'Profissional criada: {prof.nome}')
        return prof

    def _catalogo(self, prof):
        criados = 0
        procs = []
        hoje = timezone.localdate()
        for nome, categoria, duracao, descricao, preco in CATALOGO:
            proc, novo = Procedimento.objects.get_or_create(
                nome=nome,
                defaults={
                    'categoria': categoria, 'duracao_minutos': duracao,
                    'descricao': descricao, 'ativo': True,
                },
            )
            criados += int(novo)
            procs.append(proc)
            Habilitacao.objects.get_or_create(profissional=prof, procedimento=proc)
            if preco is not None and not Preco.objects.filter(
                procedimento=proc, profissional__isnull=True,
            ).exists():
                Preco.objects.create(
                    procedimento=proc, profissional=None,
                    valor=Decimal(preco), vigente_desde=hoje, descricao='Tabela base',
                )
        self.stdout.write(f'Catálogo: {criados} procedimento(s) criado(s), {len(CATALOGO) - criados} já existiam.')
        return procs

    def _agenda(self, prof):
        if DisponibilidadeProfissional.objects.filter(profissional=prof).exists():
            return
        for dia, inicio, fim in AGENDA_SEMANAL:
            DisponibilidadeProfissional.objects.create(
                profissional=prof, dia_semana=dia, hora_inicio=inicio, hora_fim=fim,
            )
        self.stdout.write('Agenda semanal: seg-sex 9h-18h, sáb 9h-15h.')

    def _termo_lgpd(self):
        if VersaoTermo.objects.filter(tipo='LGPD', procedimento__isnull=True, ativa=True).exists():
            return
        VersaoTermo.objects.create(
            tipo='LGPD', procedimento=None, versao='1.0', vigente_desde=date.today(), ativa=True,
            titulo='Termo de consentimento para tratamento de dados pessoais (LGPD)',
            conteudo=(
                'Texto de exemplo gerado pelo seed. Substitua pelo termo oficial da '
                'clínica em Painel > Termos antes de usar com clientes reais.'
            ),
        )
        self.stdout.write('Termo LGPD 1.0 criado (texto de exemplo).')

    def _anamnese(self):
        _form, novo = FormularioAnamnese.objects.get_or_create(
            nome='Anamnese padrão',
            defaults={
                'tipo': 'ANAMNESE', 'escopo': 'GLOBAL', 'schema_json': ANAMNESE_PADRAO,
                'ativo': True, 'obrigatorio': False,
            },
        )
        if novo:
            self.stdout.write('Formulário de anamnese padrão criado.')

    def _demo(self, prof, procs):
        tz = timezone.get_current_timezone()
        amanha = timezone.localdate() + timedelta(days=1)
        # proximo dia util (seg-sex) a partir de amanha
        while amanha.weekday() >= 5:
            amanha += timedelta(days=1)
        criados = 0
        for i, (nome, telefone, email) in enumerate(CLIENTES_DEMO):
            cliente, _ = Cliente.objects.get_or_create(
                telefone=telefone, defaults={'nome': nome, 'email': email},
            )
            if Atendimento.objects.filter(cliente=cliente).exists():
                continue
            proc = procs[(i * 5) % len(procs)]
            preco = Preco.objects.filter(procedimento=proc, profissional__isnull=True).first()
            valor = preco.valor if preco else None
            passado = timezone.make_aware(
                datetime.combine(timezone.localdate() - timedelta(days=7 + i), time(10 + i, 0)), tz,
            )
            futuro = timezone.make_aware(datetime.combine(amanha, time(10 + 2 * i, 0)), tz)
            for inicio, status in ((passado, Atendimento.STATUS_REALIZADO), (futuro, Atendimento.STATUS_AGENDADO)):
                fim = inicio + timedelta(minutes=proc.duracao_minutos)
                if Atendimento.objects.conflito_com(prof, inicio, fim).exists():
                    continue
                Atendimento.objects.create(
                    cliente=cliente, profissional=prof, procedimento=proc,
                    data_hora_inicio=inicio, data_hora_fim=fim,
                    valor_cobrado=valor, status=status,
                )
                criados += 1
        self.stdout.write(f'Demonstração: {criados} atendimento(s) fictício(s) criado(s).')
