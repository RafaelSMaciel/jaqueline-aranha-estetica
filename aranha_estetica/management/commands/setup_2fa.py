"""Bootstrap 2FA TOTP para um usuario.

Uso:
    python manage.py setup_2fa <email>

- Cria device TOTP confirmado
- Imprime URI otpauth:// e QR ASCII no terminal (escanear no Google Authenticator,
  Authy, 1Password, etc.)
- Gera 10 backup tokens (static device) — guarde em local seguro
- Idempotente: se device ja existe, nao recria. Para resetar use --force.
- --force = reset completo: apaga TODOS os devices OTP do usuario (TOTP de
  qualquer nome, backup) antes de criar o novo — um device plantado por quem
  teve acesso a conta nao sobrevive a recuperacao. Registrado na auditoria.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = 'Cria device TOTP + backup tokens para um usuario (bootstrap 2FA).'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str)
        parser.add_argument(
            '--force', action='store_true',
            help='Reset completo: apaga todos os devices 2FA do usuario e cria um novo.',
        )
        parser.add_argument('--name', type=str, default='default', help='Nome do device.')

    def handle(self, *args, **options):
        User = get_user_model()
        email = options['email']
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise CommandError(f'Usuario {email} nao encontrado.') from exc

        existing = TOTPDevice.objects.filter(user=user, name=options['name']).first()
        if existing and not options['force']:
            raise CommandError(
                f'TOTP ja existe para {email}. Use --force para recriar.'
            )
        if options['force']:
            from django_otp import devices_for_user

            from aranha_estetica.utils.audit import registrar_log

            removidos = list(devices_for_user(user, confirmed=None))
            for device in removidos:
                device.delete()
            if removidos:
                registrar_log(None, 'setup_2fa --force: 2FA redefinido', 'usuario', user.pk,
                              {'devices_removidos': len(removidos)})

        device = TOTPDevice.objects.create(
            user=user, name=options['name'], confirmed=True
        )

        self.stdout.write(self.style.SUCCESS(f'\nTOTP device criado para {email}\n'))
        self.stdout.write('URI (cole em app authenticator se nao puder escanear):')
        self.stdout.write(self.style.WARNING(device.config_url))
        self.stdout.write('\nQR Code (escaneie no app):')
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(device.config_url)
            qr.make(fit=True)
            qr.print_ascii(out=self.stdout, tty=False, invert=False)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'(QR nao gerado: {exc})'))

        # Backup tokens — static device
        static_dev, _ = StaticDevice.objects.get_or_create(
            user=user, name='backup', confirmed=True
        )
        static_dev.token_set.all().delete()
        tokens = [StaticToken.random_token() for _ in range(10)]
        for t in tokens:
            StaticToken.objects.create(device=static_dev, token=t)

        self.stdout.write(self.style.SUCCESS('\nBackup tokens (use 1 vez cada — guarde seguro!):'))
        for t in tokens:
            self.stdout.write(f'  {t}')
        self.stdout.write(
            '\nEntre em /admin-login/ e valide o codigo em /painel/seguranca/2fa/challenge/ '
            '(codigos de backup tambem valem).\n'
        )
