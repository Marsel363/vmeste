"""Configuration for local use and a persistent hosted deployment."""
import os
from urllib.parse import urlparse

def is_production():
    return os.getenv('APP_ENV','local').strip().lower()=='production'

def production_origin():
    value=os.getenv('RENDER_EXTERNAL_URL','').strip().rstrip('/')
    if not value:return ''
    parsed=urlparse(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('RENDER_EXTERNAL_URL must be an HTTPS origin')
    return value

def allowed_origins():
    result={x.strip().rstrip('/') for x in os.getenv('ALLOWED_ORIGINS','http://localhost:8000,http://127.0.0.1:8000').split(',') if x.strip()}
    origin=production_origin()
    if origin:result.add(origin)
    return result

def validate_hosted_config():
    if not is_production():return
    secret=os.getenv('SECRET_KEY','')
    if len(secret)<32 or secret=='REPLACE_WITH_RANDOM_SECRET':raise RuntimeError('Set a persistent SECRET_KEY of at least 32 characters')
    database=os.getenv('DATABASE_URL','')
    if not database.startswith(('postgres://','postgresql://','postgresql+psycopg://')):
        raise RuntimeError('Hosted deployment requires persistent PostgreSQL DATABASE_URL; local SQLite is not used')
    if os.getenv('COOKIE_SECURE','0')!='1':raise RuntimeError('Hosted deployment requires COOKIE_SECURE=1')

def allow_create_couple():
    return os.getenv('ALLOW_CREATE_COUPLE','0' if is_production() else '1')=='1'
