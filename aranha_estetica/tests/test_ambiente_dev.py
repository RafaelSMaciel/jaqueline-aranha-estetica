"""aranha.W014: no dev, avisa Python/pacote local fora do requirements.lock (o do CI e da imagem)."""
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from aranha_estetica.checks import check_ambiente_igual_ao_lock


class AmbienteIgualAoLockTests(SimpleTestCase):
    def _ids_e_msg(self, lock, **settings_extra):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'requirements.lock').write_text(lock, encoding='utf-8')
            with override_settings(BASE_DIR=Path(tmp), **settings_extra), \
                    patch.object(sys, 'version_info', (3, 12, 9)):
                avisos = check_ambiente_igual_ao_lock()
        return [a.id for a in avisos], ' '.join(a.msg for a in avisos)

    def test_versao_diferente_avisa(self):
        django = metadata.version('Django')
        ids, msg = self._ids_e_msg(f'# lock\nDjango=={django}\nasgiref==0.0.1\npacote-so-linux==1.0\n',
                                   DEBUG=True)
        self.assertEqual(ids, ['aranha.W014'])
        self.assertIn('asgiref', msg)
        self.assertNotIn('Django', msg)
        self.assertNotIn('pacote-so-linux', msg)   # nao instalado: lock e da imagem Linux

    def test_igual_ao_lock_ou_fora_do_dev_fica_mudo(self):
        lock_igual = f"Django=={metadata.version('Django')}\n"
        self.assertEqual(self._ids_e_msg(lock_igual, DEBUG=True)[0], [])
        self.assertEqual(self._ids_e_msg('asgiref==0.0.1\n', DEBUG=False)[0], [])
