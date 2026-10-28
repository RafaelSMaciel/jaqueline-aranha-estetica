"""Depois de restaurar um backup: reaplica o direito ao esquecimento feito DEPOIS do backup.

Restaurar um backup de ontem "ressuscita" quem foi anonimizado hoje (LGPD art. 18 VI). Fluxo:

    1. ANTES de restaurar, no banco atual (se ele ainda abre):
         python manage.py reaplicar_esquecimentos --listar --desde 2026-10-01 > esquecidos.json
       (--desde = data do backup que vai ser restaurado). Sem o banco atual, monte a lista pelos
       logs da aplicacao (linha 'Cliente <id> anonimizado') ou pelos pedidos registrados.
    2. Restaure (scripts/backup/testar_restauracao.sh mostra como).
    3. No banco restaurado:
         python manage.py reaplicar_esquecimentos esquecidos.json
       Anonimiza (LgpdService.esquecer_cliente) cada id que ainda nao esta anonimizado; os que nao
       existem no backup (cadastro posterior a ele) sao so contados.

--listar le a trilha (log_auditoria 'Cliente anonimizado...') e os cadastros ja pseudonimizados.
"""
import json
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aranha_estetica.models import Cliente, LogAuditoria
from aranha_estetica.services.lgpd import LgpdService
from aranha_estetica.utils.auditoria import registrar_log
from ...utils.datas import ler_data

ACAO_ANONIMIZADO = 'Cliente anonimizado'


class Command(BaseCommand):
    help = 'Lista (antes) ou reaplica (depois) as anonimizacoes LGPD em volta de um restore de backup.'

    def add_arguments(self, parser):
        parser.add_argument('arquivo', nargs='?', help='JSON gerado por --listar (modo reaplicar).')
        parser.add_argument('--listar', action='store_true', help='Imprime os ids anonimizados desde --desde.')
        parser.add_argument('--desde', help='Data (AAAA-MM-DD) do backup a restaurar (modo --listar).')

    def handle(self, *args, **options):
        if options['listar']:
            return self._listar(options['desde'])
        if not options['arquivo']:
            raise CommandError('Informe o arquivo de ids (gerado por --listar) ou use --listar --desde.')
        return self._reaplicar(options['arquivo'])

    def _listar(self, desde):
        if not desde:
            raise CommandError('--listar exige --desde AAAA-MM-DD (data do backup).')
        try:
            dia = ler_data(desde)
        except ValueError as exc:
            raise CommandError('--desde no formato AAAA-MM-DD.') from exc
        inicio = timezone.make_aware(datetime.combine(dia, time.min))
        pela_trilha = LogAuditoria.objects.filter(
            tabela='cliente', acao__startswith=ACAO_ANONIMIZADO, criado_em__gte=inicio,
        ).values_list('registro_id', flat=True)
        pelo_cadastro = Cliente.all_objects.filter(
            nome__startswith='[ANONIMIZADO-', deletado_em__gte=inicio,
        ).values_list('pk', flat=True)
        ids = sorted({i for i in pela_trilha if i} | set(pelo_cadastro))
        self.stdout.write(json.dumps({'desde': desde, 'clientes': ids}))

    def _reaplicar(self, caminho):
        try:
            with open(caminho, encoding='utf-8') as fh:
                ids = json.load(fh)['clientes']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CommandError(f'Arquivo invalido ({exc}): use o JSON gerado por --listar.') from exc
        feitos, ja, ausentes = [], 0, 0
        for pk in ids:
            cliente = Cliente.all_objects.filter(pk=pk).first()
            if cliente is None:
                ausentes += 1
                continue
            if (cliente.nome or '').startswith('[ANONIMIZADO-'):
                ja += 1
                continue
            LgpdService.esquecer_cliente(cliente)
            feitos.append(pk)
        registrar_log(None, 'Reaplicou esquecimentos apos restauracao de backup', 'cliente', None,
                      detalhes={'anonimizados': feitos, 'ja_anonimizados': ja, 'fora_do_backup': ausentes})
        self.stdout.write(self.style.SUCCESS(
            f'{len(feitos)} anonimizados de novo, {ja} ja estavam, {ausentes} nao existem neste backup.'))
