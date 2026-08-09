"""Alembic environment.

Alembic runs synchronously, so the asyncpg URL from DATABASE_URL is converted
to psycopg2 with `common.db.sync_url`. Forgetting this is the single most
common migration failure in this project.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from common.config import settings
from common.db import Base, sync_url

# Importing the models populates Base.metadata.
from app import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", sync_url(settings.DATABASE_URL))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
