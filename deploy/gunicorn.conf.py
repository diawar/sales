"""Run behind the existing HTTPS reverse proxy, never on a public interface."""
bind = '127.0.0.1:8173'
workers = 1  # Conservative SQLite default. Reassess before adding workers.
worker_class = 'gthread'
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
worker_tmp_dir = '/run/over-sales'
forwarded_allow_ips = '127.0.0.1,::1'
accesslog = None  # Token-bearing tracking/subscription URLs must not enter logs.
errorlog = '-'
loglevel = 'warning'
capture_output = True
