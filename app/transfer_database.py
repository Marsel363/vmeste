"""Copy the existing calendar into an EMPTY persistent database.

The source is read-only, explicit IDs and password hashes are preserved, and
the destination is never cleared/merged. No credentials are printed.
"""
from __future__ import annotations
import argparse
import getpass
import os
import sqlite3
from pathlib import Path
from sqlalchemy import create_engine,select,func,text,inspect
from sqlalchemy.engine import make_url
from dotenv import load_dotenv
from .database import Base

def normalized_url(value):
    if value.startswith('postgres://'):return value.replace('postgres://','postgresql+psycopg://',1)
    if value.startswith('postgresql://'):return value.replace('postgresql://','postgresql+psycopg://',1)
    return value

def copy_calendar(source_engine,target_engine):
    """A single target transaction; refuse every nonempty destination."""
    tables=list(Base.metadata.sorted_tables)
    with source_engine.connect() as source:
        # A read-only database from v0.1.5 predates personal tasks. Copy it
        # without modifying the source or silently skipping core tables.
        rows={table.name:[] if table.name in ('personal_tasks','lesson_exclusions','lesson_attendance','day_attendance') and not inspect(source).has_table(table.name)
              else [dict(row._mapping) for row in source.execute(select(table))] for table in tables}
    if not rows['couples'] or not rows['users']:
        raise ValueError('В исходной базе нет календаря и аккаунтов. Проверьте выбранный vmeste.db')
    with target_engine.begin() as destination:
        Base.metadata.create_all(destination)
        if any(destination.scalar(select(func.count()).select_from(table)) for table in tables):
            raise ValueError('Целевая база уже содержит данные. Перенос остановлен, ничего не удалено')
        for table in tables:
            if rows[table.name]:destination.execute(table.insert(),rows[table.name])
        if destination.dialect.name=='postgresql':
            for table in tables:
                ids=[row['id'] for row in rows[table.name]]
                destination.execute(text("SELECT setval(CAST(pg_get_serial_sequence(:name, 'id') AS regclass), :last_id, :called)"),
                                    {'name':table.name,'last_id':max(ids) if ids else 1,'called':bool(ids)})
        counts={table.name:destination.scalar(select(func.count()).select_from(table)) for table in tables}
        if counts!={name:len(items) for name,items in rows.items()}:
            raise ValueError('Проверка количества записей не прошла; перенос отменён')
    return counts

def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description='Перенос текущего календаря в пустую серверную базу')
    parser.add_argument('--source',default='vmeste.db',help='Путь к текущему локальному SQLite-файлу')
    args=parser.parse_args()
    source_path=Path(args.source).resolve()
    if not source_path.is_file():parser.error('Исходный файл vmeste.db не найден')
    # Prompt keeps the connection string out of shell history and screenshots.
    target_url=getpass.getpass('Вставьте строку подключения Neon (ввод скрыт): ').strip()
    url=make_url(normalized_url(target_url))
    if url.drivername!='postgresql+psycopg':parser.error('Требуется строка подключения PostgreSQL из Neon')
    if url.query.get('sslmode') not in ('require','verify-ca','verify-full'):
        parser.error('Используйте защищённую строку Neon с sslmode=require')
    source=create_engine('sqlite://',creator=lambda:sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True))
    target=create_engine(url,pool_pre_ping=True,connect_args={'connect_timeout':15})
    try:
        counts=copy_calendar(source,target)
        print('Перенос завершён. Аккаунты и заметки сохранены.')
        print(', '.join(f'{name}: {count}' for name,count in counts.items()))
    finally:
        source.dispose();target.dispose()

if __name__=='__main__':
    try:main()
    except ValueError as exc:
        print(str(exc));raise SystemExit(1)
    except Exception:
        print('Не удалось выполнить перенос. Проверьте доступ к Neon и строку подключения. Пароли и исходная база не менялись.')
        raise SystemExit(1)

