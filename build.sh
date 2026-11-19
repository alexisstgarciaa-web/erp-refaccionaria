#!/usr/bin/env bash
# Lo corre Render en cada publicación.
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
# Catálogo de códigos postales de Correos de México (solo carga si cambió)
python manage.py cargar_codigos_postales
# Usuario administrador inicial con DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_PASSWORD (solo la primera vez)
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
  python manage.py createsuperuser --no-input --email "" || echo "El administrador ya existe; se deja como está."
fi
