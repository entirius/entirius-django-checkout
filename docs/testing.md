# Testing

```bash
make test                                  # all tests
DATABASE_URL=postgresql://... make test    # custom database
```

Tests need PostgreSQL (default `postgresql://postgres:postgres@localhost:5432/test`).

`make test-legacy` runs the suite without `django_access`, on the legacy key path.
