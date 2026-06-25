# Django stack skill

Follow app boundaries already present. Use migrations for schema changes, forms/serializers for validation, and queryset filtering for authorization. Avoid signals for core business workflows when explicit services are clearer. Test permissions, migrations, and transaction behavior. Keep settings environment-driven and never commit `SECRET_KEY` or production credentials.
