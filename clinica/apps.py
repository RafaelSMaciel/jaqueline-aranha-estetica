"""AppConfigs de projeto."""
from django.contrib.staticfiles.apps import StaticFilesConfig


class StaticFilesSemFonteConfig(StaticFilesConfig):
    """staticfiles sem publicar aranha_estetica/static/src (fonte do Vite).

    O fonte e compilado p/ static/dist no build. Coletado, o `@import "tailwindcss"`
    de src/css/app.css quebra o collectstatic com ManifestStaticFilesStorage
    (MissingFileError) e ainda exporia o fonte em /static/src/.
    Obs.: o padrao casa com QUALQUER diretorio chamado 'src' nos statics.
    """

    ignore_patterns = [*StaticFilesConfig.ignore_patterns, 'src']
